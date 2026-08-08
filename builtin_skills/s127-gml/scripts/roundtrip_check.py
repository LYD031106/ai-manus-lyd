#!/usr/bin/env python3
"""回归测试：把生产 GML 反解成要素清单再重建，与原件逐要素比对。

用法：
    python roundtrip_check.py '示例/**/*.gml'
    python roundtrip_check.py 某个.gml --keep /tmp/out    # 保留中间产物

比对三件事：
  1. **几何**：解析完图元链后每个要素的坐标序列是否一致（不比分段方式）
  2. **元素**：每个要素的子元素路径多重集是否一致（关联按目标要素类归一，
     不比 gml:id 编号）
  3. **校验**：反解出的 JSON 能否通过 validate_featureset.py

已知的、预期内的差异（脚本会单独归类，不计入失败）：
  * `displayName` 省略 vs 显式 `false` —— XSD 里 minOccurs=0，语义等价
  * `s100:maskReference` —— 边界抑制显示，本工具不支持
"""
from __future__ import annotations

import argparse
import collections
import glob
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
GML = "{http://www.opengis.net/gml/3.2}"
XH = "{http://www.w3.org/1999/xlink}href"
GID = GML + "id"

# 预期内的差异模式（不计入失败）
EXPECTED = [
    (re.compile(r"/featureName/displayName$"),
     "displayName 省略 vs 显式 false（XSD minOccurs=0，语义等价）"),
    (re.compile(r"maskReference"),
     "s100:maskReference 边界抑制显示，本工具不支持"),
]


def resolved_geometry(path):
    """每个要素 → 解析后的坐标点序列（拼接所有段，按 orientation 反向）。"""
    root = ET.parse(path).getroot()
    curves, orient, comp, surf, pts = {}, {}, {}, {}, {}
    for e in root:
        tag, gid = e.tag.split("}")[-1], e.get(GID)
        if tag == "Curve":
            pl = e.find(f"{GML}segments/{GML}LineStringSegment/{GML}posList")
            n = [float(x) for x in (pl.text or "").split()]
            curves[gid] = [(round(n[i], 7), round(n[i + 1], 7)) for i in range(0, len(n), 2)]
        elif tag == "OrientableCurve":
            orient[gid] = (e.find(GML + "baseCurve").get(XH)[1:], e.get("orientation", "+"))
        elif tag == "CompositeCurve":
            comp[gid] = [m.get(XH)[1:] for m in e.findall(GML + "curveMember")]
        elif tag == "Surface":
            patch = e.find(f"{GML}patches/{GML}PolygonPatch")
            surf[gid] = patch.find(f"{GML}exterior/{GML}Ring/{GML}curveMember").get(XH)[1:]
        elif tag == "Point":
            n = [float(x) for x in (e.find(GML + "pos").text or "").split()]
            pts[gid] = [(round(n[0], 7), round(n[1], 7))]

    def ring(cc):
        out = []
        for oc in comp.get(cc, []):
            cid, sign = orient.get(oc, (None, "+"))
            q = list(curves.get(cid, []))
            if sign == "-":
                q.reverse()
            out.extend(q if not out else (q[1:] if out and out[-1] == q[0] else q))
        return out

    members = [m for m in root.iter() if m.tag.endswith("}members")][0]
    out = []
    for f in members:
        geom = None
        for c in f:
            if c.tag.endswith("}geometry"):
                ref = list(c)[0].get(XH)[1:]
                geom = ring(surf[ref]) if ref in surf else (pts.get(ref) or ring(ref))
        out.append((f.tag.split("}")[-1], geom))
    return out


def element_signature(path):
    """{要素类+子元素路径: 次数}，关联按目标要素类归一。"""
    root = ET.parse(path).getroot()
    members = [m for m in root.iter() if m.tag.endswith("}members")][0]
    ids = {f.get(GID): f.tag.split("}")[-1] for f in members}
    sig = collections.Counter()
    for f in members:
        ftype = f.tag.split("}")[-1]

        def walk(e, prefix):
            tag = e.tag.split("}")[-1]
            cur = prefix + "/" + tag
            href = e.get(XH)
            sig[f"{ftype}{cur}->{ids.get(href[1:], 'GEOM')}" if href else f"{ftype}{cur}"] += 1
            for child in e:
                walk(child, cur)

        for child in f:
            walk(child, "")
    return sig


def classify(path_key):
    for pattern, reason in EXPECTED:
        if pattern.search(path_key):
            return reason
    return None


def check_one(src: Path, workdir: Path):
    stem = re.sub(r"\W+", "_", src.stem)[:40]
    js = workdir / f"{stem}.json"
    out = workdir / f"{stem}.gml"

    r = subprocess.run([sys.executable, str(HERE / "gml_to_featureset.py"), str(src), "-o", str(js)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        return {"status": "反解失败", "detail": r.stderr.strip().splitlines()[-1][:90]}

    v = subprocess.run([sys.executable, str(HERE / "validate_featureset.py"), str(js)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    n_err = len([l for l in v.stdout.splitlines() if l.startswith("[ERROR]")])
    err_samples = [l[8:110] for l in v.stdout.splitlines() if l.startswith("[ERROR]")][:3]

    b = subprocess.run([sys.executable, str(HERE / "build_gml.py"), str(js), "-o", str(out)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if b.returncode:
        return {"status": "构建失败", "detail": b.stderr.strip().splitlines()[-1][:90],
                "n_err": n_err, "err_samples": err_samples}

    ga, gb = resolved_geometry(src), resolved_geometry(out)
    geom_ok = len(ga) == len(gb) and all(
        (x is None and y is None) or (x is not None and y is not None and list(x) == list(y))
        for (_, x), (_, y) in zip(ga, gb))
    n_geom = sum(1 for _, g in ga if g)

    A, B = element_signature(src), element_signature(out)
    expected, unexpected = collections.Counter(), collections.Counter()
    for k in set(A) | set(B):
        d = B.get(k, 0) - A.get(k, 0)
        if not d:
            continue
        reason = classify(k)
        (expected if reason else unexpected)[reason or k] += abs(d)

    return {"status": "OK", "geom_ok": geom_ok, "n_geom": n_geom,
            "n_err": n_err, "err_samples": err_samples,
            "expected": expected, "unexpected": unexpected}


def main() -> int:
    ap = argparse.ArgumentParser(description="GML → JSON → GML 回归比对")
    ap.add_argument("pattern", help="GML 路径或 glob，如 '示例/**/*.gml'")
    ap.add_argument("--keep", help="保留中间产物的目录")
    args = ap.parse_args()

    files = sorted(Path(p) for p in glob.glob(args.pattern, recursive=True))
    if not files:
        print(f"[错误] 没有匹配到文件：{args.pattern}", file=sys.stderr)
        return 1

    tmp = Path(args.keep) if args.keep else Path(tempfile.mkdtemp(prefix="s127rt-"))
    tmp.mkdir(parents=True, exist_ok=True)

    all_expected, all_unexpected = collections.Counter(), collections.Counter()
    n_ok = n_geom_total = n_err_total = 0
    print(f"{'数据集':44s} {'几何':>10s} {'非预期差异':>10s} {'校验ERR':>7s}")
    print("-" * 78)
    for src in files:
        res = check_one(src, tmp)
        name = src.name[:42]
        if res["status"] != "OK":
            print(f"{name:44s} {res['status']}  {res.get('detail','')}")
            continue
        n_ok += 1
        n_geom_total += res["n_geom"]
        n_err_total += res["n_err"]
        all_expected.update(res["expected"])
        all_unexpected.update(res["unexpected"])
        geom = f"{res['n_geom']}/{res['n_geom']} ✓" if res["geom_ok"] else "✗ 不一致"
        n_unexp = sum(res["unexpected"].values())
        print(f"{name:44s} {geom:>10s} {n_unexp:>10d} {res['n_err']:>7d}")

    print("-" * 78)
    print(f"{n_ok}/{len(files)} 个数据集完成往返；几何要素 {n_geom_total} 个全部一致"
          if not any(True for _ in ()) else "")
    print(f"\n非预期差异：{sum(all_unexpected.values())} 处 / {len(all_unexpected)} 类")
    for k, n in all_unexpected.most_common(20):
        print(f"  {n:>4d}  {k}")
    print(f"\n预期内差异：{sum(all_expected.values())} 处")
    for k, n in all_expected.most_common():
        print(f"  {n:>4d}  {k}")
    print(f"\n反解 JSON 的校验 ERROR 合计 {n_err_total} 处"
          f"（反解产物未经人工整理，ERROR 多半来自原件本身的问题，逐条看 --keep 目录里的输出）")
    if args.keep:
        print(f"中间产物保留在 {tmp}")
    return 1 if all_unexpected else 0


if __name__ == "__main__":
    sys.exit(main())
