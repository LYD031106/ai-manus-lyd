"""海事法规 PDF / 图片解析工具。

把沙箱里的 PDF 或图片送多模态 API，提取成结构化 Markdown 再写回沙箱。
只处理确实需要模型的格式 —— 扫描件 OCR、表格结构识别、读图上的坐标标注。

Word / Excel / CSV 不走本工具：那是确定性的文本提取，在沙箱内用
`/opt/skills/s127-gml/scripts/parse_office.py` 直接跑，不必消耗 API token。
Word 里的内嵌图片由该脚本导出成图片文件后，再回到本工具解析。
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
你是海事法规文本提取专家。用户会给你一份或多份中国海事主管机关的法规文件（PDF 或图片），
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
- 如果 PDF 是扫描件或图片质量差，尽力提取，无法辨认的用 [?] 标注
- 多个文件时，标注每段内容来自哪个文件（用文件名或文号标注）
"""

COORDS_PROMPT = """\
这份文件是坐标附件。请只提取所有地理坐标，输出为 JSON 数组：
```json
[{"name": "区域/线/点名称", "points": [[纬度, 经度], ...]}]
```
度分秒转十进制，精度 7 位。坐标顺序 [纬度, 经度]。
如果有多个区域/航路/报告线，按名称分组。
"""

# 可直接作为 image 块发送的格式；其余按 PDF 处理
_IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

_OFFICE_SUFFIXES = {".docx", ".doc", ".wps", ".csv", ".xlsx", ".xls", ".xlsm"}


class ParseRegulationToolkit(BaseToolkit):
    """海事法规 PDF / 图片解析工具"""

    name: str = "parse_regulation"
    instructions: str = """
- 本工具只用来读**图**：示意图、扫描件。只接受 .pdf / .png / .jpg / .gif / .webp
- **先跑本地脚本，再决定要不要用本工具**：
  `python3 /opt/skills/s127-gml/scripts/parse_office.py <所有文件...> -o <输出.md> --image-dir <图件目录>`
  该脚本处理 PDF/Word/Excel/CSV 的文本，并在输出的 `>` 提示行里写明还需不需要调本工具
- **有文本层的 PDF 不要传进来** —— 文本层是无损的，过 OCR 反而会被改写；
  这类 PDF 只把脚本导出的图件 PNG 传给本工具
- 只在两种情况下调本工具：① 脚本导出的图件 PNG；② 脚本判定为扫描件的整份 PDF
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
        """用多模态模型解析 PDF 或图片，提取为结构化 Markdown 并写入沙箱。用于从法规通告、服务指南、坐标附件、示意图中提取正文、适用船舶类别、报告制度和地理坐标。

        Args:
            files: 待解析的文件路径列表，绝对路径，只支持 .pdf/.png/.jpg/.gif/.webp
            output: 解析结果的输出路径，绝对路径（.md 或 .json）
            coords_only: （可选）只提取坐标，输出 JSON 而非完整 Markdown
            model: （可选）覆盖默认的解析模型
        """
        if not files:
            return ToolResult(success=False, message="files 不能为空")

        # 先校验格式，避免下载完才发现不支持
        for path in files:
            suffix = PurePosixPath(path).suffix.lower()
            if suffix in _OFFICE_SUFFIXES:
                return ToolResult(
                    success=False,
                    message=f"{PurePosixPath(path).name} 是 Office 文档，本工具不处理。请用 shell 执行："
                            f"python3 /opt/skills/s127-gml/scripts/parse_office.py "
                            f"{path} -o <输出.md> --image-dir <图件目录>"
                            f"（该脚本抽文本并导出图件，之后只把导出的图片传给本工具）",
                )
            if suffix != ".pdf" and suffix not in _IMAGE_MIME:
                return ToolResult(
                    success=False,
                    message=f"不支持的格式 {suffix or '(无后缀)'}：{PurePosixPath(path).name}。"
                            f"本工具只接受 .pdf 与 .png/.jpg/.gif/.webp",
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
        """把文件内容以 base64 塞进 Messages API 请求体，返回提取出的文本。

        注意送出去的是文件字节的 base64，不是路径或链接 —— 沙箱内的路径
        对外部服务不可达。
        """
        settings = get_settings()
        api_key = settings.parse_api_key or settings.api_key
        if not api_key:
            raise RuntimeError("未配置 PARSE_API_KEY 或 API_KEY，无法解析 PDF/图片")

        base = (settings.parse_api_base or settings.api_base or "https://api.anthropic.com").rstrip("/")
        if not base.endswith("/v1"):
            base = f"{base}/v1"

        content: List[Dict[str, Any]] = []
        for path, data in files:
            name = PurePosixPath(path).name
            suffix = PurePosixPath(path).suffix.lower()
            encoded = base64.standard_b64encode(data).decode("ascii")
            # 每份材料前先报文件名，否则多文件时模型无法标注内容出自哪一份
            content.append({"type": "text", "text": f"以下是文件《{name}》："})
            if suffix in _IMAGE_MIME:
                content.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": _IMAGE_MIME[suffix], "data": encoded},
                })
            else:
                content.append({
                    "type": "document",
                    "source": {"type": "base64", "media_type": "application/pdf", "data": encoded},
                })

        content.append({
            "type": "text",
            "text": "请提取这份文件中的所有坐标。" if coords_only
            else "请按要求提取这份法规文件的内容。",
        })

        payload = {
            "model": model or settings.parse_model,
            "max_tokens": settings.parse_max_tokens,
            "system": COORDS_PROMPT if coords_only else SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": content}],
        }

        logger.info("parse_regulation: 送 %d 个文件到 %s", len(files), payload["model"])
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(
                f"{base}/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=payload,
            )
            if response.status_code >= 400:
                # 带上响应体，否则网关的 400/404 只剩状态码，排查不了
                raise RuntimeError(
                    f"{base}/messages 返回 {response.status_code}：{response.text[:500]}"
                )
            body = response.json()

        text = "".join(
            block.get("text", "")
            for block in body.get("content", [])
            if block.get("type") == "text"
        )
        usage = body.get("usage") or {}
        logger.info("parse_regulation: 输入 %s tokens，输出 %s tokens",
                    usage.get("input_tokens"), usage.get("output_tokens"))
        if not text.strip():
            raise RuntimeError("模型未返回任何文本内容")
        return text
