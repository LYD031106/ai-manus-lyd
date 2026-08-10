"""海事法规图件解析工具（多模态 API）。

**只收图片。** 把沙箱里的 PNG/JPG 送多模态模型，提取成结构化 Markdown 写回沙箱。
用于读示意图上的坐标标注、扫描件页面、图表结构 —— 这些是脚本做不到的部分。

文本一律不走本工具：有文本层的 PDF、Word、Excel、CSV 都由沙箱内的
`/opt/skills/s127-gml/scripts/parse_office.py` 本地抽取（无损、零 token）。
该脚本同时负责把需要模型看的部分转成 PNG：

* Word 内嵌图 → 按尺寸过滤后导出
* 有文本层的 PDF → 只渲染含大图的页
* 扫描件 PDF → 整份逐页渲染

因此本工具的输入永远是图片，走阿里云百炼的图片通道即可 —— base64 直传、
不必先存 OSS。（百炼的 PDF 通道要用业务空间专属地址且分地域，本工具刻意不碰。）
"""
import base64
import logging
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.core.config import get_settings
from app.domain.external.sandbox import Sandbox
from app.domain.models.tool_result import ToolResult
from app.domain.services.tools.base import BaseToolkit, tool

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """\
你是海事法规文本提取专家。用户会给你一张或多张中国海事主管机关法规文件的图件（示意图、扫描页），
请按以下格式输出：

## 文件基本信息
- 发文机构：
- 文号：
- 印发日期：
- 生效日期：
- 失效日期（如有）：
- 文件标题：

## 正文
逐条提取原文内容，保留条款编号（第X条/第X款）。对表格用 Markdown 表格格式。
每一条独立段落，便于后续按条款溯源。

## 适用船舶（如有）
如果文件定义了适用船舶类别，逐项列出：
- 类别名称
- 判定条件（如有具体标准）
- 出处条款

## 报告制度（如有）
如果文件规定了船舶报告制度，按报告类型分项：
- 报告类型（驶入/抵达/变化/引航/其他）
- 报告时机
- 报告对象
- 报告方式（VHF频道等）
- 报告内容
- 出处条款

## 坐标（如有）
如果文件包含地理坐标（经纬度），提取为 JSON 数组，格式：
```json
[{"name": "区域/点名称", "points": [[纬度, 经度], ...]}]
```
度分秒转十进制，精度 7 位。

## 图件说明（如有）
描述文件中包含的示意图、地图的内容与标注。

注意：
- 输出语言与原文一致（中文为主）
- 坐标顺序为 [纬度, 经度]（与 S-127 GML 一致）
- 图片质量差时尽力提取，无法辨认的用 [?] 标注；不要凭猜测补全坐标数字
- 多张图时，标注每段内容来自哪张图（用给出的文件名标注）
"""

COORDS_PROMPT = """\
这些图件是坐标附件或示意图。请只提取所有地理坐标，输出为 JSON 数组：
```json
[{"name": "区域/线/点名称", "points": [[纬度, 经度], ...]}]
```
度分秒转十进制，精度 7 位。坐标顺序 [纬度, 经度]。
如果有多个区域/航路/报告线，按名称分组。
"""

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


class ParseRegulationToolkit(BaseToolkit):
    """海事法规图件解析工具（多模态）"""

    name: str = "parse_regulation"
    instructions: str = """
- 本工具**只收图片**（.png/.jpg/.gif/.webp/.bmp），用来读示意图上的坐标标注、扫描件页面
- **任何文件都先跑本地脚本**，它抽文本并把该看的图导出成 PNG：
  `python3 /opt/skills/s127-gml/scripts/parse_office.py <所有文件...> -o <输出.md> --image-dir <图件目录>`
- 脚本会在输出的 `>` 提示行里写明「已导出以下图片，请用 parse_regulation 解析」
  或「全为装饰图，无需调用」—— 照它说的做，**不要自己猜**
- **不要把 PDF / Word 传进本工具**：有文本层的文件本地抽取是无损的，过 OCR 反而被改写；
  扫描件也由脚本渲染成 PNG 后再传进来
- files 与 output 都用绝对路径；用户上传的附件在 /home/ubuntu/upload/ 下
- 只需要地理坐标时加 coords_only=true，可跳过全文解析
- 工具只负责产出 Markdown，解析完再用文件工具读 output 查看内容
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
        """用多模态模型读图件，提取为结构化 Markdown 并写入沙箱。用于读示意图上的坐标与范围标注、扫描件页面。只接受图片，先用 parse_office.py 把源文件的图件导出成 PNG。

        Args:
            files: 待解析的图片路径列表，绝对路径，只支持 .png/.jpg/.gif/.webp/.bmp
            output: 解析结果的输出路径，绝对路径（.md 或 .json）
            coords_only: （可选）只提取坐标，输出 JSON 而非完整 Markdown
            model: （可选）覆盖默认的解析模型
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

        try:
            text = await self._call_api(loaded, bool(coords_only), model)
        except Exception as exc:
            logger.exception("多模态解析失败")
            return ToolResult(success=False, message=f"多模态解析失败：{exc}")

        result = await self.sandbox.file_write(file=output, content=text)
        if not result.success:
            return result

        return ToolResult(
            success=True,
            message=f"已解析 {len(loaded)} 个文件，结果写入 {output}",
            data={"output": output},
        )

    async def _call_api(
        self,
        files: List[Tuple[str, bytes]],
        coords_only: bool,
        model: Optional[str],
    ) -> str:
        """调阿里云百炼的 OpenAI 兼容接口读图，返回提取出的文本。

        图片以 base64 data URL 内联在请求体里，不传路径也不传链接 —— 沙箱内的
        路径对外部服务不可达，而 data URL 方式不需要先上传到 OSS。
        """
        settings = get_settings()
        api_key = settings.parse_api_key or settings.api_key
        if not api_key:
            raise RuntimeError("未配置 PARSE_API_KEY（阿里云百炼 API Key），无法解析图件")

        base = (settings.parse_api_base or DEFAULT_API_BASE).rstrip("/")
        url = f"{base}/chat/completions"

        content: List[Dict[str, Any]] = []
        for path, data in files:
            mime = _IMAGE_MIME[PurePosixPath(path).suffix.lower()]
            encoded = base64.standard_b64encode(data).decode("ascii")
            # 每张图前先报文件名，否则多图时模型无法标注内容出自哪一张
            content.append({"type": "text", "text": f"以下是图件《{PurePosixPath(path).name}》："})
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{encoded}"},
            })
        content.append({
            "type": "text",
            "text": "请提取这些图件中的所有坐标。" if coords_only
            else "请按要求提取这些图件的内容。",
        })

        payload = {
            "model": model or settings.parse_model,
            "max_tokens": settings.parse_max_tokens,
            # 兼容格式下 system 是一条 message，不是顶层字段
            "messages": [
                {"role": "system", "content": COORDS_PROMPT if coords_only else SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
        }

        logger.info("parse_regulation: 送 %d 张图到 %s", len(files), payload["model"])
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
                json=payload,
            )
            if response.status_code >= 400:
                # 带上响应体，否则 400/404 只剩状态码，排查不了
                raise RuntimeError(f"{url} 返回 {response.status_code}：{response.text[:500]}")
            body = response.json()

        choices = body.get("choices") or []
        text = ((choices[0].get("message") or {}).get("content") or "") if choices else ""
        if isinstance(text, list):  # 少数版本把 content 返回成分块列表
            text = "".join(b.get("text", "") for b in text if isinstance(b, dict))
        usage = body.get("usage") or {}
        logger.info("parse_regulation: 输入 %s tokens，输出 %s tokens",
                    usage.get("prompt_tokens"), usage.get("completion_tokens"))
        if not text.strip():
            raise RuntimeError(f"模型未返回任何文本内容；原始响应：{str(body)[:300]}")
        return text
