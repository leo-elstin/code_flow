"""Parses the XML accessibility tree returned by WebDriverAgent's
`/session/{id}/source` route into a flat, indexed element list with bounds,
suitable for drawing an overlay and mapping tap coordinates.
"""
import xml.etree.ElementTree as ET
from dataclasses import dataclass


@dataclass
class ParsedElement:
    index: int
    type: str
    label: str | None
    x: float
    y: float
    width: float
    height: float


@dataclass
class ParsedTree:
    root_width: float
    root_height: float
    elements: list[ParsedElement]


class HierarchyParseError(RuntimeError):
    pass


def parse(xml_str: str) -> ParsedTree:
    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError as exc:
        raise HierarchyParseError(f"Could not parse WDA source XML: {exc}") from exc

    elements: list[ParsedElement] = []
    for node in root.iter():
        attrs = node.attrib
        try:
            x = float(attrs.get("x", "0"))
            y = float(attrs.get("y", "0"))
            width = float(attrs.get("width", "0"))
            height = float(attrs.get("height", "0"))
        except ValueError:
            continue
        if width <= 0 or height <= 0:
            continue
        label = attrs.get("label") or attrs.get("name") or attrs.get("value") or None
        elements.append(
            ParsedElement(
                index=len(elements),
                type=attrs.get("type", node.tag),
                label=label,
                x=x,
                y=y,
                width=width,
                height=height,
            )
        )

    if not elements:
        return ParsedTree(root_width=0.0, root_height=0.0, elements=[])

    root_el = elements[0]
    return ParsedTree(root_width=root_el.width, root_height=root_el.height, elements=elements)
