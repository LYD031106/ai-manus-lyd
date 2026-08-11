"""海事法规图件文字识别工具（阿里云百炼 OCR）。

**只收图片，只做忠实转录。** 把沙箱里的 PNG/JPG 逐张送 `qwen-vl-ocr`，
把识别出的原文按图分节写回沙箱，不做归纳、不改写、不套格式。

用 OCR 模型而非通用 VL 模型，是因为实测通用模型会改写原文：

    原文（以入库 GML 为准）   qwen-vl-max      qwen-vl-ocr
    采取避让行动时             采取让行动时 ✗     采取避让行动时 ✓
    船载自动识别系统           船舶自动识别系统 ✗  船载自动识别系统 ✓

法规条文不能被改写，所以本工具不给模型自由发挥的提示词 —— 用官方默认
prompt「只输出图中文本」。结构化（分要素、提坐标、判专题）由 agent 拿到
原文后自己做，那一步有源文可核，比让识别模型顺手总结安全。

文本一律不走本工具：有文本层的 PDF、Word、Excel、CSV 都由沙箱内的
`/opt/skills/s127-gml/scripts/parse_office.py` 本地抽取（无损、零 token）。
该脚本同时负责把需要识别的部分转成 PNG：

* Word 内嵌图 → 按尺寸过滤后导出
* 有文本层的 PDF → 只渲染含大图的页
* 扫描件 PDF → 整份逐页渲染

因此本工具的输入永远是图片，走百炼的图片通道即可 —— base64 直传、不必先存 OSS。
（百炼的 PDF 通道要用业务空间专属地址且分地域，本工具刻意不碰。）

注意 `qwen-vl-ocr` 的 max_tokens 上限 4096，所以**逐张调用**而非一次多图。
"""
import base64
import logging
from pathlib import PurePosixPath
from typing import List, Optional, Tuple

import httpx

from app.core.config import get_settings
from app.domain.external.sandbox import Sandbox
from app.domain.models.tool_result import ToolResult
from app.domain.services.tools.base import BaseToolkit, tool

logger = logging.getLogger(__name__)


# qwen-vl-ocr 的官方默认 prompt：只输出图中文本，不加描述、不套格式。
# 刻意不写"提取发文机构/适用船舶/坐标"之类的结构化要求 —— 那会诱导模型
# 归纳与推断（实测通用模型据此把"未标注日期"写成"根据文号推断为2026年"）。
OCR_PROMPT = "Read all the text in the image."

# 只提坐标时给一句约束，但仍以转录为主，不让它换算或分组
COORDS_PROMPT = "Read all the text in the image, especially any latitude/longitude coordinates. Output them exactly as printed."

_IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}

_OFFICE_SUFFIXES = {".docx", ".doc", ".wps", ".csv", ".xlsx", ".xls", ".xlsm"}

_SCRIPT = "/opt/skills/s127-gml/scripts/parse_office.py"

# 百炼的 OpenAI 兼容端点。图片用它即可，无需业务空间专属地址
DEFAULT_API_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"

# qwen-vl-ocr 的输出上限就是 4096（提到 8192 需向阿里云商务申请）
OCR_MAX_TOKENS = 4096
# 图像像素上下限：小图放大避免小字糊掉，大图缩小避免超限。取官方示例值
MIN_PIXELS = 32 * 32 * 3
MAX_PIXELS = 32 * 32 * 8192


class ParseRegulationToolkit(BaseToolkit):
    """海事法规图件文字识别工具（OCR）"""

    name: str = "parse_regulation"
    instructions: str = """
- 本工具**只收图片**（.png/.jpg/.gif/.webp/.bmp），做的是**文字识别**：
  逐张返回图中原文，不做归纳、不分要素、不换算坐标
- **任何文件都先跑本地脚本**，它抽文本并把该识别的图导出成 PNG：
  `python3 /opt/skills/s127-gml/scripts/parse_office.py <所有文件...> -o <输出.md> --image-dir <图件目录>`
- 脚本会在输出的 `>` 提示行里写明「已导出以下图片，请用 parse_regulation 解析」
  或「全为装饰图，无需调用」—— 照它说的做，**不要自己猜**
- **不要把 PDF / Word 传进本工具**：有文本层的文件本地抽取是无损的；
  扫描件也由脚本渲染成 PNG 后再传进来
- 拿到识别结果后，**由你**去分要素、判专题、把度分秒换算成十进制 ——
  识别模型只负责照抄原文，结构化是你的活，这样有原文可核
- 输出里若出现「本页输出已达上限」的提示，说明该页没识别完，
  把那页图裁成上下两半重新识别
- files 与 output 都用绝对路径；用户上传的附件在 /home/ubuntu/upload/ 下
- 只需要坐标时加 coords_only=true
"""

    def __init__(self, sandbox: Sandbox):
        """初始化解析工具

        Args:
            sandbox: 沙箱服务，用于读取源文件与写回解析结果
        """
        super().__init__()
        self.sandbox = sandbox

    @tool(parse_docstring=True)
    async def parse_regulation(
        self,
        files: List[str],
        output: str,
        coords_only: Optional[bool] = False,
        model: Optional[str] = None,
    ) -> ToolResult:
        """对图件做文字识别（OCR），逐张返回图中原文并按图分节写入沙箱。用于读扫描件页面、示意图上的坐标与范围标注。只接受图片，先用 parse_office.py 把源文件的图件导出成 PNG。返回的是原文，分要素与坐标换算由你自己做。

        Args:
            files: 待识别的图片路径列表，绝对路径，只支持 .png/.jpg/.gif/.webp/.bmp
            output: 识别结果的输出路径，绝对路径（.md）
            coords_only: （可选）提示模型重点关注经纬度，仍按原样转录不做换算
            model: （可选）覆盖默认的识别模型
        """
        if not files:
            return ToolResult(success=False, message="files 不能为空")

        # 先校验格式，避免下载完才发现不支持
        for path in files:
            suffix = PurePosixPath(path).suffix.lower()
            name = PurePosixPath(path).name
            if suffix == ".pdf" or suffix in _OFFICE_SUFFIXES:
                return ToolResult(
                    success=False,
                    message=f"{name} 不是图片，本工具只收图片。先用 shell 执行："
                            f"python3 {_SCRIPT} {path} -o <输出.md> --image-dir <图件目录>"
                            f"；该脚本会本地抽取文本（无损、零 token），"
                            f"并把需要模型看的部分渲染成 PNG，再把那些 PNG 传给本工具",
                )
            if suffix not in _IMAGE_MIME:
                return ToolResult(
                    success=False,
                    message=f"不支持的格式 {suffix or '(无后缀)'}：{name}。"
                            f"本工具只接受 {'/'.join(sorted(_IMAGE_MIME))}",
                )

        loaded: List[Tuple[str, bytes]] = []
        for path in files:
            try:
                stream = await self.sandbox.file_download(path)
                loaded.append((path, stream.read()))
            except Exception as exc:
                logger.warning("下载文件失败 %s: %s", path, exc)
                return ToolResult(success=False, message=f"无法读取文件 {path}：{exc}")

        settings = get_settings()
        api_key = settings.parse_api_key or settings.api_key
        if not api_key:
            return ToolResult(
                success=False,
                message="未配置 PARSE_API_KEY（阿里云百炼 API Key），无法识别图件")

        prompt = COORDS_PROMPT if coords_only else OCR_PROMPT
        sections: List[str] = []
        failed: List[str] = []
        async with httpx.AsyncClient(timeout=300.0) as client:
            for path, data in loaded:
                name = PurePosixPath(path).name
                try:
                    text = await self._ocr_one(client, path, data, prompt, api_key,
                                               model or settings.parse_model, settings)
                except Exception as exc:
                    logger.exception("识别失败 %s", name)
                    failed.append(f"{name}（{exc}）")
                    continue
                sections.append(f"# {name}\n\n{text}")

        if not sections:
            return ToolResult(success=False,
                              message="所有图件识别失败：" + "；".join(failed))

        result = await self.sandbox.file_write(
            file=output, content="\n\n---\n\n".join(sections))
        if not result.success:
            return result

        msg = f"已识别 {len(sections)}/{len(loaded)} 张图，结果写入 {output}"
        if failed:
            msg += f"；失败 {len(failed)} 张：" + "；".join(failed)
        return ToolResult(success=True, message=msg,
                          data={"output": output, "ok": len(sections), "failed": len(failed)})

    async def _ocr_one(self, client, path: str, data: bytes, prompt: str,
                       api_key: str, model: str, settings) -> str:
        """识别单张图。

        逐张调用而非一次多图：qwen-vl-ocr 的 max_tokens 上限 4096，
        多图共用一次输出很容易被截断，且截断处无法定位到具体哪张图。
        """
        base = (settings.parse_api_base or DEFAULT_API_BASE).rstrip("/")
        url = f"{base}/chat/completions"
        mime = _IMAGE_MIME[PurePosixPath(path).suffix.lower()]
        encoded = base64.standard_b64encode(data).decode("ascii")

        payload = {
            "model": model,
            "max_tokens": min(settings.parse_max_tokens, OCR_MAX_TOKENS),
            "messages": [{"role": "user", "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{encoded}"},
                    # 太小的图先放大再识别，太大的先缩小，避免小字糊掉或超限
                    "min_pixels": MIN_PIXELS,
                    "max_pixels": MAX_PIXELS,
                },
                {"type": "text", "text": prompt},
            ]}],
        }

        response = await client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            json=payload,
        )
        if response.status_code >= 400:
            # 带上响应体，否则 400/404 只剩状态码，排查不了
            raise RuntimeError(f"{url} 返回 {response.status_code}：{response.text[:300]}")
        body = response.json()

        choices = body.get("choices") or []
        text = ((choices[0].get("message") or {}).get("content") or "") if choices else ""
        if isinstance(text, list):  # 少数版本把 content 返回成分块列表
            text = "".join(b.get("text", "") for b in text if isinstance(b, dict))
        usage = body.get("usage") or {}
        n_out = usage.get("completion_tokens") or 0
        logger.info("parse_regulation[%s]: 输入 %s / 输出 %s tokens",
                    PurePosixPath(path).name, usage.get("prompt_tokens"), n_out)
        if not text.strip():
            raise RuntimeError(f"模型未返回任何文本内容；原始响应：{str(body)[:300]}")
        # 命中输出上限说明这一页没识别完，必须告知调用方而不是静默交付半页
        if n_out >= min(settings.parse_max_tokens, OCR_MAX_TOKENS):
            text += (f"\n\n> ⚠️ 本页输出已达上限 {n_out} tokens，内容可能被截断。"
                     f"如为长文档页，建议把该页拆成上下两半重新识别。")
        return text
