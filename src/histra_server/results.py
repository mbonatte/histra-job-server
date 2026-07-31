from __future__ import annotations

import math
from typing import Any


def extract_numeric_series(value: Any, *, max_points: int = 100000, max_series: int = 500) -> list[dict[str, Any]]:
    """Find plottable one-dimensional numeric arrays in arbitrary result envelopes."""
    output: list[dict[str, Any]]=[]
    def walk(node: Any, path: str) -> None:
        if len(output) >= max_series: return
        if isinstance(node, list) and 1 < len(node) <= max_points and all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(float(v)) for v in node):
            output.append({"path":path or "/","values":[float(v) for v in node],"count":len(node)})
            return
        if isinstance(node, dict):
            for key,item in node.items(): walk(item, f"{path}/{str(key).replace('~','~0').replace('/','~1')}")
        elif isinstance(node, list):
            for index,item in enumerate(node):
                if isinstance(item,(dict,list)): walk(item,f"{path}/{index}")
    walk(value,"")
    return output
