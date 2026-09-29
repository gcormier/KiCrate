"""Enclosure data model. All lengths are millimetres.

Coordinate system for a PCB mount: origin at the board centre, +X along the
enclosure length, +Y along the width, looking down onto the component side.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = 1

Vec2 = tuple[float, float]
Pos = Annotated[float, Field(gt=0)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Source(Model):
    url: str
    kind: Literal["drawing", "product_page", "series_page", "cad", "other"] = "drawing"
    sha256: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")
    retrieved: dt.date | None = None


class Box3(Model):
    length: Pos
    width: Pos
    height: Pos
    note: str | None = None


class Notches(Model):
    """Rectangular notch cut from each of the four board corners."""

    size: tuple[Pos, Pos] = Field(description="Notch extent along X and Y")
    outer_radius: float = Field(0, ge=0, description="Fillet on the notch's convex corners (the ones on the board edge)")
    inner_radius: float = Field(0, ge=0, description="Fillet on the notch's concave (inside) corner")


class EdgeNotch(Model):
    """Rectangular notch cut into one board edge (e.g. to clear a lid-screw boss)."""

    edge: Literal["x_min", "x_max", "y_min", "y_max"]
    at: float = Field(description="Notch centre along the edge (X for y_* edges, Y for x_* edges)")
    width: Pos
    depth: Pos
    outer_radius: float = Field(0, ge=0, description="Fillet where the notch meets the board edge")
    inner_radius: float = Field(0, ge=0, description="Fillet on the notch's inside corners")


class RectOutline(Model):
    shape: Literal["rect"] = "rect"
    size: tuple[Pos, Pos]
    corner_radius: float = Field(0, ge=0)
    corner_notches: Notches | None = None
    edge_notches: list[EdgeNotch] = Field(default_factory=list)


class PolygonOutline(Model):
    shape: Literal["polygon"] = "polygon"
    points: list[tuple[float, float] | tuple[float, float, float]] = Field(
        min_length=3, description="[x, y] or [x, y, fillet_radius], counter-clockwise"
    )


Outline = Annotated[Union[RectOutline, PolygonOutline], Field(discriminator="shape")]


class Hole(Model):
    at: Vec2
    drill: Pos
    boss_diameter: Pos | None = Field(None, description="Standoff/boss the board rests on (bottom-side keepout)")
    head_diameter: Pos | None = Field(None, description="Screw head (top-side keepout)")
    screw: str | None = Field(None, description='Intended fastener as the manufacturer states it, e.g. "#4 x 1/4 self-tapping"')
    footprint: str | None = Field(None, description='Override, e.g. "MountingHole:MountingHole_3.2mm_M3"')
    plated: bool = False


class HolePattern(Model):
    """Rectangular pattern of four holes centred on the board."""

    pitch: tuple[Pos, Pos]
    center: Vec2 = (0.0, 0.0)
    drill: Pos
    boss_diameter: Pos | None = None
    head_diameter: Pos | None = None
    screw: str | None = None
    footprint: str | None = None
    plated: bool = False

    def holes(self) -> list[Hole]:
        dx, dy = self.pitch[0] / 2, self.pitch[1] / 2
        cx, cy = self.center
        common = self.model_dump(exclude={"pitch", "center"})
        return [Hole(at=(cx + sx * dx, cy + sy * dy), **common) for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]


class Keepout(Model):
    """Area where components must not be placed on the given side(s)."""

    side: Literal["top", "bottom", "both"] = "both"
    rect: tuple[float, float, float, float] | None = Field(None, description="x_min, y_min, x_max, y_max")
    circle: tuple[float, float, Pos] | None = Field(None, description="x, y, diameter")
    edge: Literal["x_min", "x_max", "y_min", "y_max"] | None = Field(None, description="Band along a board edge")
    width: Pos | None = Field(None, description="Band width for `edge`")
    max_height: float | None = Field(None, ge=0, description="Components allowed up to this height instead of none")
    reason: str | None = None

    @model_validator(mode="after")
    def _one_shape(self):
        n = sum(x is not None for x in (self.rect, self.circle, self.edge))
        if n != 1:
            raise ValueError("keepout needs exactly one of rect, circle, edge")
        if self.edge and self.width is None:
            raise ValueError("edge keepout needs width")
        return self


class Slots(Model):
    """Card-guide slots the board slides into (board edges along X)."""

    slot_width: Pos | None = None
    pcb_thickness: tuple[Pos, Pos] | None = Field(None, description="min, max board thickness")
    levels: list[float] = Field(default_factory=list, description="Board bottom height above the inside floor, per slot level")


class PcbMount(Model):
    id: str = Field(pattern=r"^[a-z0-9_-]+$")
    type: Literal["bosses", "card_guide_slots", "floor", "lid"]
    description: str | None = None
    outline: Outline
    holes: list[Hole] = Field(default_factory=list)
    hole_pattern: HolePattern | None = None
    keepouts: list[Keepout] = Field(default_factory=list)
    slots: Slots | None = None
    z: float | None = Field(None, description="Board bottom height above the inside floor")
    notes: list[str] = Field(default_factory=list)

    def all_holes(self) -> list[Hole]:
        return self.holes + (self.hole_pattern.holes() if self.hole_pattern else [])


class PanelHole(Model):
    at: Vec2
    drill: Pos
    countersink: tuple[Pos, float] | None = Field(None, description="diameter, angle in degrees")
    screw: str | None = None


class Panel(Model):
    """A removable face. Coordinates: origin at panel centre, viewed from outside."""

    id: str = Field(pattern=r"^[a-z0-9_-]+$")
    faces: list[Literal["front", "back", "left", "right", "top", "bottom"]]
    material: str | None = None
    thickness: Pos | None = None
    outline: Outline
    holes: list[PanelHole] = Field(default_factory=list)
    usable_area: tuple[Pos, Pos] | None = Field(None, description="Visible/cuttable area (e.g. bezel opening), centred")
    notes: list[str] = Field(default_factory=list)


class Enclosure(Model):
    schema_version: Literal[1] = SCHEMA_VERSION
    mfr: str = Field(pattern=r"^[a-z0-9-]+$")
    part: str
    variants: list[str] = Field(default_factory=list, description="Orderable part numbers sharing this geometry")
    series: str
    description: str | None = None
    url: str | None = None
    material: str | None = None
    provenance: Literal["scraped", "manual", "assisted"]
    verified: bool = False
    last_checked: dt.date | None = None
    sources: list[Source] = Field(default_factory=list)
    outer: Box3
    inner: Box3 | None = None
    pcb_mounts: list[PcbMount] = Field(default_factory=list)
    panels: list[Panel] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_ids(self):
        for kind, items in (("pcb_mounts", self.pcb_mounts), ("panels", self.panels)):
            ids = [i.id for i in items]
            if len(ids) != len(set(ids)):
                raise ValueError(f"duplicate {kind} id")
        return self

    @property
    def key(self) -> str:
        return f"{self.mfr}/{self.part}"
