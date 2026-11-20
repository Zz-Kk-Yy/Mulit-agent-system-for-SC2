from __future__ import annotations

import json
import os
import random
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")


def _patch_random_shuffle_for_pysc2() -> None:
    """Keep pysc2's old random.shuffle(x, random) call working on Python 3.11+."""
    if sys.version_info < (3, 11):
        return
    if getattr(random, "_swarmbrain_shuffle_compat", False):
        return

    original_shuffle = random.shuffle

    def shuffle_with_compat(seq, randfunc=None):
        if randfunc is None:
            return original_shuffle(seq)
        for idx in range(len(seq) - 1, 0, -1):
            swap_idx = int(randfunc() * (idx + 1))
            seq[idx], seq[swap_idx] = seq[swap_idx], seq[idx]
        return None

    random.shuffle = shuffle_with_compat
    random._swarmbrain_shuffle_compat = True


_patch_random_shuffle_for_pysc2()

import pygame
from pysc2.lib.features import MINIMAP_FEATURES
from s2clientprotocol import sc2api_pb2 as sc_pb
from sc2.client import Client
from sc2.data import Race
from sc2.portconfig import Portconfig
from sc2.renderer import Renderer


@dataclass(frozen=True)
class FeatureCardSpec:
    key: str
    index: int
    title: str
    subtitle: str
    footer_title: str
    footer_note: str
    accent: tuple[int, int, int]
    shadow: tuple[int, int, int]
    palette_mode: str

    @property
    def name(self) -> str:
        return str(self.key)


FEATURE_CARD_SPECS: tuple[FeatureCardSpec, ...] = (
    FeatureCardSpec(
        key="height_map",
        index=int(MINIMAP_FEATURES.height_map.index),
        title="HeightMap",
        subtitle="height_map",
        footer_title="terrain height",
        footer_note="dark=low, bright=high",
        accent=(186, 129, 72),
        shadow=(74, 46, 23),
        palette_mode="terrain",
    ),
    FeatureCardSpec(
        key="player_relative",
        index=int(MINIMAP_FEATURES.player_relative.index),
        title="Relation",
        subtitle="player_relative",
        footer_title="player relation",
        footer_note="1=self, 2=ally, 3=neutral, 4=enemy",
        accent=(244, 41, 41),
        shadow=(90, 20, 20),
        palette_mode="player_relative",
    ),
    FeatureCardSpec(
        key="unit_type",
        index=100,
        title="UnitType",
        subtitle="unit_type",
        footer_title="unit type IDs",
        footer_note="same ID -> same class",
        accent=(52, 244, 94),
        shadow=(6, 52, 16),
        palette_mode="categorical",
    ),
    FeatureCardSpec(
        key="unit_hit_points",
        index=101,
        title="HitPoints",
        subtitle="unit_hit_points",
        footer_title="unit health",
        footer_note="brighter = more HP",
        accent=(245, 98, 177),
        shadow=(79, 31, 60),
        palette_mode="scalar",
    ),
    FeatureCardSpec(
        key="unit_density",
        index=102,
        title="Density",
        subtitle="unit_density",
        footer_title="unit density",
        footer_note="brighter = more overlap",
        accent=(68, 218, 255),
        shadow=(11, 56, 72),
        palette_mode="scalar",
    ),
)


class LiveFeatureMapExporter:
    CARD_WIDTH = 124
    CARD_HEIGHT = 244
    CONTENT_SIZE = 106
    HEADER_HEIGHT = 40
    FOOTER_HEIGHT = 50
    BODY_TOP = HEADER_HEIGHT + 2
    MINIMAP_SIZE = 336

    def __init__(self, output_dir: Path, every_n: int = 1) -> None:
        self.output_dir = Path(output_dir)
        self.every_n = max(1, int(every_n))
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._pygame_ready = False
        self._title_font: pygame.font.Font | None = None
        self._subtitle_font: pygame.font.Font | None = None
        self._footer_font: pygame.font.Font | None = None
        self._meta_font: pygame.font.Font | None = None
        self._warned_missing_layers = False
        self.latest_frame_dir: Path | None = None
        self.latest_metadata: dict[str, Any] | None = None
        self._world_width = 1.0
        self._world_height = 1.0
        self._world_origin_x = 0.0
        self._world_origin_y = 0.0

    def export(self, response_observation: sc_pb.ResponseObservation, iteration: int) -> None:
        if iteration % self.every_n != 0:
            return

        observation = response_observation.observation
        if not observation.HasField("feature_layer_data"):
            if not self._warned_missing_layers:
                print("Feature-layer data was not requested; skipping feature map export.")
                self._warned_missing_layers = True
            return

        self._ensure_pygame()

        game_loop = int(observation.game_loop)
        frame_dir = self.output_dir / f"loop_{game_loop:06d}"
        frame_dir.mkdir(parents=True, exist_ok=True)

        feature_records: list[dict[str, Any]] = []
        default_shape = self._infer_default_shape(observation)
        for spec in FEATURE_CARD_SPECS:
            plane = self._extract_feature_plane(spec, observation, default_shape)
            if plane is None:
                continue

            card = self._render_feature_map(spec, plane)
            target_path = frame_dir / f"{spec.index:02d}_{spec.name}.png"
            pygame.image.save(card, str(target_path))
            feature_records.append(
                {
                    "index": spec.index,
                    "name": spec.name,
                    "title": spec.title,
                    "footer_title": spec.footer_title,
                    "footer_note": spec.footer_note,
                    "shape": list(plane.shape),
                    "dtype": str(plane.dtype),
                    "min": int(np.min(plane)),
                    "max": int(np.max(plane)),
                    "path": target_path.name,
                }
            )

        minimap_record = self._export_minimap(frame_dir, observation)
        vision_record = self._build_vision_metadata(observation)

        metadata = {
            "iteration": int(iteration),
            "game_loop": game_loop,
            "time_seconds": round(game_loop / 22.4, 3),
            "time_formatted": self._format_game_time(game_loop),
            "features": feature_records,
        }
        if minimap_record:
            metadata["minimap"] = minimap_record
        if vision_record:
            metadata["vision"] = vision_record
        metadata_path = frame_dir / "frame.json"
        metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
        self.latest_frame_dir = frame_dir
        self.latest_metadata = metadata

    def configure_map_bounds(self, game_info: Any) -> None:
        pathing_grid = getattr(game_info, "pathing_grid", None)
        if pathing_grid is not None:
            self._world_width = max(1.0, float(getattr(pathing_grid, "width", 1) or 1))
            self._world_height = max(1.0, float(getattr(pathing_grid, "height", 1) or 1))

        playable_area = getattr(game_info, "playable_area", None)
        if playable_area is None:
            return

        p0 = getattr(playable_area, "p0", None)
        p1 = getattr(playable_area, "p1", None)
        if p0 is not None:
            self._world_origin_x = float(getattr(p0, "x", 0.0) or 0.0)
            self._world_origin_y = float(getattr(p0, "y", 0.0) or 0.0)
        if p0 is not None and p1 is not None:
            self._world_width = max(1.0, float(getattr(p1, "x", self._world_width) or self._world_width) - self._world_origin_x)
            self._world_height = max(1.0, float(getattr(p1, "y", self._world_height) or self._world_height) - self._world_origin_y)

    def _ensure_pygame(self) -> None:
        if self._pygame_ready:
            return
        pygame.init()
        pygame.font.init()
        title_font_path = pygame.font.match_font("Lucida Console") or pygame.font.match_font("Consolas")
        subtitle_font_path = pygame.font.match_font("Lucida Console") or pygame.font.match_font("Consolas")
        meta_font_path = pygame.font.match_font("Consolas") or pygame.font.match_font("Courier New")
        self._title_font = pygame.font.Font(title_font_path, 14)
        self._subtitle_font = pygame.font.Font(subtitle_font_path, 11)
        self._footer_font = pygame.font.Font(meta_font_path, 11)
        self._meta_font = pygame.font.Font(meta_font_path, 9)
        self._pygame_ready = True

    def _render_feature_map(self, spec: FeatureCardSpec, plane: np.ndarray) -> pygame.Surface:
        colored = self._colorize_plane(spec, plane)
        image_surface = pygame.surfarray.make_surface(np.transpose(colored, (1, 0, 2)))
        return pygame.transform.scale(image_surface, (self.MINIMAP_SIZE, self.MINIMAP_SIZE))

    def _infer_default_shape(self, observation: Any) -> tuple[int, int]:
        for feature in (MINIMAP_FEATURES.height_map, MINIMAP_FEATURES.player_relative, MINIMAP_FEATURES.camera):
            plane = feature.unpack(observation)
            if plane is not None:
                return tuple(int(value) for value in plane.shape)
        return (64, 64)

    def _extract_feature_plane(
        self,
        spec: FeatureCardSpec,
        observation: Any,
        default_shape: tuple[int, int],
    ) -> np.ndarray | None:
        if spec.key == "height_map":
            return MINIMAP_FEATURES.height_map.unpack(observation)
        if spec.key == "player_relative":
            return MINIMAP_FEATURES.player_relative.unpack(observation)
        if spec.key == "unit_type":
            return self._build_unit_type_plane(observation, default_shape)
        if spec.key == "unit_hit_points":
            return self._build_unit_hit_points_plane(observation, default_shape)
        if spec.key == "unit_density":
            return self._build_unit_density_plane(observation, default_shape)
        return None

    def _iter_visible_units(self, observation: Any):
        raw_data = getattr(observation, "raw_data", None)
        units = getattr(raw_data, "units", []) if raw_data is not None else []
        for unit in units:
            pos = getattr(unit, "pos", None)
            if pos is None:
                continue
            display_type = int(getattr(unit, "display_type", 1) or 1)
            if display_type == 2:
                continue
            yield unit

    def _project_world_to_grid(self, x: float, y: float, shape: tuple[int, int]) -> tuple[int, int]:
        height, width = shape
        usable_width = max(self._world_width, 1.0)
        usable_height = max(self._world_height, 1.0)
        normalized_x = (float(x) - self._world_origin_x) / usable_width
        normalized_y = (float(y) - self._world_origin_y) / usable_height
        col = int(np.clip(round(normalized_x * (width - 1)), 0, width - 1))
        row = int(np.clip(round(normalized_y * (height - 1)), 0, height - 1))
        return row, col

    def _build_unit_type_plane(self, observation: Any, shape: tuple[int, int]) -> np.ndarray:
        plane = np.zeros(shape, dtype=np.int32)
        density = np.zeros(shape, dtype=np.int32)
        for unit in self._iter_visible_units(observation):
            pos = getattr(unit, "pos", None)
            if pos is None:
                continue
            row, col = self._project_world_to_grid(pos.x, pos.y, shape)
            density[row, col] += 1
            current = int(plane[row, col])
            incoming = int(getattr(unit, "unit_type", 0) or 0)
            if density[row, col] == 1 or incoming >= current:
                plane[row, col] = incoming
        return plane

    def _build_unit_hit_points_plane(self, observation: Any, shape: tuple[int, int]) -> np.ndarray:
        plane = np.zeros(shape, dtype=np.int32)
        for unit in self._iter_visible_units(observation):
            pos = getattr(unit, "pos", None)
            if pos is None:
                continue
            row, col = self._project_world_to_grid(pos.x, pos.y, shape)
            hp = int(getattr(unit, "health", 0) or 0)
            plane[row, col] = max(plane[row, col], hp)
        return plane

    def _build_unit_density_plane(self, observation: Any, shape: tuple[int, int]) -> np.ndarray:
        plane = np.zeros(shape, dtype=np.int32)
        for unit in self._iter_visible_units(observation):
            pos = getattr(unit, "pos", None)
            if pos is None:
                continue
            row, col = self._project_world_to_grid(pos.x, pos.y, shape)
            plane[row, col] += 1
        return plane

    def _render_card(self, spec: FeatureCardSpec, plane: np.ndarray, game_loop: int) -> pygame.Surface:
        assert self._title_font is not None
        assert self._subtitle_font is not None
        assert self._footer_font is not None
        assert self._meta_font is not None

        surface = pygame.Surface((self.CARD_WIDTH, self.CARD_HEIGHT))
        surface.fill((37, 37, 37))

        header_rect = pygame.Rect(0, 0, self.CARD_WIDTH, self.HEADER_HEIGHT)
        footer_rect = pygame.Rect(0, self.CARD_HEIGHT - self.FOOTER_HEIGHT, self.CARD_WIDTH, self.FOOTER_HEIGHT)
        content_rect = pygame.Rect(0, self.BODY_TOP, self.CARD_WIDTH, footer_rect.top - self.BODY_TOP)

        pygame.draw.rect(surface, (42, 42, 42), header_rect)
        pygame.draw.rect(surface, (34, 34, 34), footer_rect)
        pygame.draw.rect(surface, (0, 0, 0), content_rect)
        pygame.draw.line(surface, spec.accent, (0, header_rect.bottom - 2), (self.CARD_WIDTH, header_rect.bottom - 2), 2)

        title_text = self._title_font.render(f"[{spec.index}] {spec.title}", False, spec.accent)
        subtitle_text = self._subtitle_font.render(spec.subtitle, False, (201, 201, 201))
        surface.blit(title_text, (6, 5))
        surface.blit(subtitle_text, (6, 22))

        colored = self._colorize_plane(spec, plane)
        image_surface = pygame.surfarray.make_surface(np.transpose(colored, (1, 0, 2)))
        image_surface = pygame.transform.scale(image_surface, (self.CONTENT_SIZE, self.CONTENT_SIZE))

        image_x = content_rect.x + (content_rect.width - self.CONTENT_SIZE) // 2
        image_y = content_rect.y + (content_rect.height - self.CONTENT_SIZE) // 2 + 1
        surface.blit(image_surface, (image_x, image_y))

        tag_rect = pygame.Rect(6, footer_rect.y + 5, self.CARD_WIDTH - 12, 16)
        pygame.draw.rect(surface, (28, 28, 28), tag_rect)
        pygame.draw.rect(surface, spec.accent, tag_rect, width=1)
        footer_text = self._footer_font.render(spec.footer_title, False, (241, 241, 241))
        footer_text_rect = footer_text.get_rect(center=tag_rect.center)
        surface.blit(footer_text, footer_text_rect)

        self._blit_wrapped_text(
            surface,
            spec.footer_note,
            self._meta_font,
            (216, 216, 216),
            pygame.Rect(6, footer_rect.y + 25, self.CARD_WIDTH - 12, self.FOOTER_HEIGHT - 8),
        )

        return surface

    def _colorize_plane(self, spec: FeatureCardSpec, plane: np.ndarray) -> np.ndarray:
        if spec.palette_mode == "player_relative":
            return self._colorize_player_relative(plane)
        if spec.palette_mode == "visibility":
            return self._colorize_visibility(plane)
        if spec.palette_mode == "creep":
            return self._colorize_binary(plane, active=(174, 102, 255), base=(12, 8, 20))
        if spec.palette_mode == "camera":
            return self._colorize_binary(plane, active=(92, 232, 255), base=(7, 19, 26))
        if spec.palette_mode == "categorical":
            return self._colorize_categorical(plane, spec.accent, spec.shadow)
        if spec.palette_mode == "terrain":
            return self._colorize_terrain(plane)
        return self._colorize_scalar(plane, spec.accent, spec.shadow)

    def _export_minimap(self, frame_dir: Path, observation: Any) -> dict[str, Any] | None:
        height = MINIMAP_FEATURES.height_map.unpack(observation)
        visibility = MINIMAP_FEATURES.visibility_map.unpack(observation)
        creep = MINIMAP_FEATURES.creep.unpack(observation)
        camera = MINIMAP_FEATURES.camera.unpack(observation)
        player_relative = MINIMAP_FEATURES.player_relative.unpack(observation)

        if height is None and player_relative is None:
            return None

        minimap_surface = self._render_minimap(
            height=height,
            visibility=visibility,
            creep=creep,
            camera=camera,
            player_relative=player_relative,
        )
        target_path = frame_dir / "minimap_live.png"
        pygame.image.save(minimap_surface, str(target_path))

        shape_source = next(
            (
                plane
                for plane in (height, visibility, creep, camera, player_relative)
                if plane is not None
            ),
            None,
        )
        if shape_source is None:
            return None

        vis_grid: list[int] | None = None
        vis_shape: list[int] | None = None
        if visibility is not None and visibility.size > 0:
            vis_grid = visibility.astype(np.int8).flatten().tolist()
            vis_shape = list(visibility.shape)  # [H, W]

        return {
            "title": "Live Minimap",
            "name": "minimap_live",
            "shape": list(shape_source.shape),
            "path": target_path.name,
            "visibilityGrid": vis_grid,
            "visibilityShape": vis_shape,
        }

    def _render_minimap(
        self,
        *,
        height: np.ndarray | None,
        visibility: np.ndarray | None,
        creep: np.ndarray | None,
        camera: np.ndarray | None,
        player_relative: np.ndarray | None,
    ) -> pygame.Surface:
        source = next(
            (
                plane
                for plane in (height, visibility, creep, camera, player_relative)
                if plane is not None
            ),
            None,
        )
        assert source is not None

        shape = source.shape
        rgb = np.zeros(shape + (3,), dtype=np.float32)

        if height is not None:
            values = height.astype(np.float32, copy=False)
            if np.max(values) > np.min(values):
                normalized = (values - np.min(values)) / (np.max(values) - np.min(values))
            else:
                normalized = np.zeros_like(values, dtype=np.float32)
            normalized = normalized * normalized * (3.0 - 2.0 * normalized)
            low = np.array([15, 22, 30], dtype=np.float32)
            mid = np.array([44, 71, 78], dtype=np.float32)
            high = np.array([104, 132, 138], dtype=np.float32)
            mid_mix = np.clip(normalized / 0.62, 0.0, 1.0)[..., None]
            hi_mix = np.clip((normalized - 0.38) / 0.62, 0.0, 1.0)[..., None]
            rgb = low + (mid - low) * mid_mix
            rgb = rgb + (high - rgb) * hi_mix

            grad_y, grad_x = np.gradient(values)
            lighting = np.tanh((-0.72 * grad_x - 0.45 * grad_y) / 18.0)
            contour = np.sin((normalized * 6.4 + 0.08) * np.pi) * 0.05
            shade = np.clip(0.84 + lighting * 0.15 + contour, 0.58, 1.16)[..., None]
            rgb = np.clip(rgb * shade, 0, 255)
        else:
            rgb[:] = np.array([18, 26, 34], dtype=np.float32)

        if creep is not None:
            creep_mask = creep.astype(bool, copy=False)
            creep_tint = np.array([130, 76, 172], dtype=np.float32)
            rgb[creep_mask] = rgb[creep_mask] * 0.48 + creep_tint * 0.52

        if visibility is not None:
            visibility_values = visibility.astype(np.int32, copy=False)
            visibility_scale = np.array([0.05, 0.24, 0.78, 1.0], dtype=np.float32)
            clipped_visibility = np.clip(visibility_values, 0, len(visibility_scale) - 1)
            rgb *= visibility_scale[clipped_visibility][..., None]

        if player_relative is not None:
            relation = player_relative.astype(np.int32, copy=False)
            overlay_palette = {
                1: np.array([74, 240, 114], dtype=np.float32),
                2: np.array([92, 187, 255], dtype=np.float32),
                4: np.array([255, 98, 98], dtype=np.float32),
            }
            for key, color in overlay_palette.items():
                mask = relation == key
                if np.any(mask):
                    rgb[mask] = rgb[mask] * 0.14 + color * 0.86

        if camera is not None:
            camera_mask = camera.astype(bool, copy=False)
            if np.any(camera_mask):
                padded = np.pad(camera_mask, 1, mode="constant", constant_values=False)
                interior = (
                    padded[1:-1, 1:-1]
                    & padded[:-2, 1:-1]
                    & padded[2:, 1:-1]
                    & padded[1:-1, :-2]
                    & padded[1:-1, 2:]
                )
                edge = camera_mask & ~interior
                padded_edge = np.pad(edge, 1, mode="constant", constant_values=False)
                halo = (
                    padded_edge[1:-1, 1:-1]
                    | padded_edge[:-2, 1:-1]
                    | padded_edge[2:, 1:-1]
                    | padded_edge[1:-1, :-2]
                    | padded_edge[1:-1, 2:]
                    | padded_edge[:-2, :-2]
                    | padded_edge[:-2, 2:]
                    | padded_edge[2:, :-2]
                    | padded_edge[2:, 2:]
                ) & ~edge
                rgb[halo] = rgb[halo] * 0.58 + np.array([78, 224, 255], dtype=np.float32) * 0.42
                rgb[edge] = np.array([112, 248, 255], dtype=np.float32)

        border_mask = np.zeros(shape, dtype=bool)
        border_mask[[0, -1], :] = True
        border_mask[:, [0, -1]] = True
        rgb[border_mask] = np.array([20, 31, 36], dtype=np.float32)

        rgb_uint8 = np.clip(rgb, 0, 255).astype(np.uint8)
        minimap_surface = pygame.surfarray.make_surface(np.transpose(rgb_uint8, (1, 0, 2)))
        minimap_surface = pygame.transform.scale(minimap_surface, (self.MINIMAP_SIZE, self.MINIMAP_SIZE))
        return minimap_surface

    @staticmethod
    def _build_vision_metadata(observation: Any) -> dict[str, Any] | None:
        visibility = MINIMAP_FEATURES.visibility_map.unpack(observation)
        if visibility is None or visibility.size <= 0:
            return None

        values = visibility.astype(np.int32, copy=False)
        visible_tiles = int(np.count_nonzero(values >= 2))
        total_tiles = int(values.size)
        visible_pct = visible_tiles / total_tiles if total_tiles else 0.0
        return {
            "visible_tiles_pct": round(visible_pct, 4),
            "fog_of_war_pct": round(max(0.0, 1.0 - visible_pct), 4),
            "visible_tiles": visible_tiles,
            "total_tiles": total_tiles,
        }

    @staticmethod
    def _colorize_scalar(
        plane: np.ndarray,
        accent: tuple[int, int, int],
        shadow: tuple[int, int, int],
    ) -> np.ndarray:
        values = plane.astype(np.float32, copy=False)
        out = np.zeros(values.shape + (3,), dtype=np.uint8)
        mask = values > 0
        if not np.any(mask):
            return out

        positive = values[mask]
        min_value = float(np.min(positive))
        max_value = float(np.max(positive))
        if max_value <= min_value:
            normalized = np.zeros_like(values, dtype=np.float32)
            normalized[mask] = 1.0
        else:
            normalized = np.zeros_like(values, dtype=np.float32)
            normalized[mask] = (values[mask] - min_value) / (max_value - min_value)
        normalized = np.power(np.clip(normalized, 0.0, 1.0), 0.8)

        shadow_arr = np.asarray(shadow, dtype=np.float32)
        accent_arr = np.asarray(accent, dtype=np.float32)
        blended = shadow_arr + (accent_arr - shadow_arr) * normalized[..., None]
        out[mask] = np.clip(blended[mask], 0, 255).astype(np.uint8)
        return out

    @staticmethod
    def _colorize_terrain(plane: np.ndarray) -> np.ndarray:
        values = plane.astype(np.float32, copy=False)
        out = np.zeros(values.shape + (3,), dtype=np.uint8)
        mask = values > 0
        if not np.any(mask):
            return out

        positive = values[mask]
        min_value = float(np.min(positive))
        max_value = float(np.max(positive))
        normalized = np.zeros_like(values, dtype=np.float32)
        if max_value <= min_value:
            normalized[mask] = 1.0
        else:
            normalized[mask] = (values[mask] - min_value) / (max_value - min_value)
        normalized = np.clip(normalized, 0.0, 1.0)
        normalized = normalized * normalized * (3.0 - 2.0 * normalized)

        base_dark = np.array([69, 42, 21], dtype=np.float32)
        base_mid = np.array([138, 95, 52], dtype=np.float32)
        base_light = np.array([214, 169, 110], dtype=np.float32)
        mid_mix = np.clip(normalized / 0.62, 0.0, 1.0)[..., None]
        hi_mix = np.clip((normalized - 0.38) / 0.62, 0.0, 1.0)[..., None]
        base = base_dark + (base_mid - base_dark) * mid_mix
        base = base + (base_light - base) * hi_mix

        grad_y, grad_x = np.gradient(values)
        light = (-0.72 * grad_x - 0.48 * grad_y)
        light = np.tanh(light / 22.0)
        contour = np.sin((normalized * 5.5 + 0.12) * np.pi) * 0.035
        shading = np.clip(0.88 + light * 0.16 + contour, 0.68, 1.16)[..., None]
        base = np.clip(base * shading, 0, 255)

        edge_falloff = np.clip(normalized[..., None] * 0.18 + 0.82, 0.78, 1.0)
        out[mask] = np.clip(base[mask] * edge_falloff[mask], 0, 255).astype(np.uint8)
        return out

    @staticmethod
    def _colorize_categorical(
        plane: np.ndarray,
        accent: tuple[int, int, int],
        shadow: tuple[int, int, int],
    ) -> np.ndarray:
        values = plane.astype(np.int32, copy=False)
        max_value = int(np.max(values))
        if max_value <= 0:
            return np.zeros(values.shape + (3,), dtype=np.uint8)

        ids = np.arange(max_value + 1, dtype=np.float32)
        mix = 0.28 + 0.67 * (((ids * 47) % 89) / 88.0)
        palette = np.zeros((max_value + 1, 3), dtype=np.float32)
        shadow_arr = np.asarray(shadow, dtype=np.float32)
        accent_arr = np.asarray(accent, dtype=np.float32)
        palette[:] = shadow_arr + (accent_arr - shadow_arr) * mix[:, None]
        palette[:, 0] *= 0.56 + (((ids * 11) % 7) / 20.0)
        palette[:, 2] *= 0.42 + (((ids * 17) % 9) / 30.0)
        palette[0] = 0
        return np.clip(palette[values], 0, 255).astype(np.uint8)

    @staticmethod
    def _colorize_player_relative(plane: np.ndarray) -> np.ndarray:
        palette = np.array(
            [
                [0, 0, 0],
                [38, 224, 72],
                [230, 213, 70],
                [128, 168, 192],
                [244, 41, 41],
            ],
            dtype=np.uint8,
        )
        values = plane.astype(np.int32, copy=False)
        clipped = np.clip(values, 0, len(palette) - 1)
        return palette[clipped]

    @staticmethod
    def _colorize_visibility(plane: np.ndarray) -> np.ndarray:
        palette = np.array(
            [
                [6, 9, 14],
                [34, 48, 66],
                [86, 124, 168],
                [198, 236, 255],
            ],
            dtype=np.uint8,
        )
        values = plane.astype(np.int32, copy=False)
        clipped = np.clip(values, 0, len(palette) - 1)
        return palette[clipped]

    @staticmethod
    def _colorize_binary(
        plane: np.ndarray,
        *,
        active: tuple[int, int, int],
        base: tuple[int, int, int],
    ) -> np.ndarray:
        values = plane.astype(np.float32, copy=False)
        mask = values > 0
        out = np.zeros(values.shape + (3,), dtype=np.uint8)
        out[:] = np.asarray(base, dtype=np.uint8)
        if np.any(mask):
            out[mask] = np.asarray(active, dtype=np.uint8)
        return out

    @staticmethod
    def _blit_wrapped_text(
        surface: pygame.Surface,
        text: str,
        font: pygame.font.Font,
        color: tuple[int, int, int],
        rect: pygame.Rect,
    ) -> None:
        x = rect.x
        y = rect.y
        line_height = font.get_linesize()
        current = ""
        for word in text.split():
            candidate = word if not current else f"{current} {word}"
            candidate_width, _ = font.size(candidate)
            if current and candidate_width > rect.width:
                rendered = font.render(current, False, color)
                surface.blit(rendered, (x, y))
                y += line_height
                current = word
                if y + line_height > rect.bottom:
                    return
            else:
                current = candidate
        if current and y + line_height <= rect.bottom:
            rendered = font.render(current, False, color)
            surface.blit(rendered, (x, y))

    @staticmethod
    def _format_game_time(game_loop: int) -> str:
        total_seconds = int(game_loop / 22.4)
        return f"t={total_seconds // 60:02}:{total_seconds % 60:02}"


def _env_flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def feature_layer_enabled() -> bool:
    """Feature-layer join turns the SC2 window into a camera-only viewer. Off by default."""
    if _env_flag("SWARMBRAIN_DISABLE_FEATURE_LAYER", "0"):
        return False
    return _env_flag("SWARMBRAIN_ENABLE_FEATURE_LAYER", "0")


def build_live_feature_map_exporter() -> LiveFeatureMapExporter | None:
    if not feature_layer_enabled():
        return None
    enabled = os.getenv("SWARMBRAIN_EXPORT_FEATURE_MAPS", "1").lower()
    if enabled in {"0", "false", "no"}:
        return None

    root = Path(os.getenv("SWARMBRAIN_FEATURE_MAPS_DIR", "./videos/feature_maps"))
    session_name = os.getenv("SWARMBRAIN_FEATURE_MAPS_SESSION") or datetime.now().strftime("%Y%m%d_%H%M%S")
    every_n = int(os.getenv("SWARMBRAIN_FEATURE_EVERY_N", "1"))
    return LiveFeatureMapExporter(root / session_name, every_n=every_n)


def enable_feature_layer_join_patch(screen_size: int = 64, minimap_size: int = 64) -> None:
    if getattr(Client.join_game, "_swarmbrain_interactive_join_patch", False):
        return

    use_feature_layer = feature_layer_enabled()
    steal_selection = _env_flag("SWARMBRAIN_RAW_AFFECTS_SELECTION", "0")
    if use_feature_layer:
        print(
            "[sc2-ui] feature-layer 已开启：窗口通常只能拖镜头，不能切视角/控兵。",
            flush=True,
        )
    else:
        print(
            "[sc2-ui] 普通可交互星际窗口：可点小地图切视角，可框选/热键控兵。"
            "机器人下令可能覆盖你的指令。请勿点退出。",
            flush=True,
        )

    async def patched_join_game(
        self,
        name: str | None = None,
        race: Race | None = None,
        observed_player_id: int | None = None,
        portconfig: Portconfig | None = None,
        rgb_render_config: dict[str, Any] | None = None,
    ):
        ifopts = sc_pb.InterfaceOptions(
            raw=True,
            score=True,
            show_cloaked=True,
            show_burrowed_shadows=True,
            raw_affects_selection=steal_selection,
            raw_crop_to_playable_area=False,
            show_placeholders=True,
        )
        if use_feature_layer:
            ifopts.feature_layer.width = int(screen_size)
            ifopts.feature_layer.resolution.x = int(screen_size)
            ifopts.feature_layer.resolution.y = int(screen_size)
            ifopts.feature_layer.minimap_resolution.x = int(minimap_size)
            ifopts.feature_layer.minimap_resolution.y = int(minimap_size)

        if rgb_render_config:
            assert isinstance(rgb_render_config, dict)
            assert "window_size" in rgb_render_config and "minimap_size" in rgb_render_config
            window_size = rgb_render_config["window_size"]
            minimap_render_size = rgb_render_config["minimap_size"]
            self._renderer = Renderer(self, window_size, minimap_render_size)
            map_width, map_height = window_size
            minimap_width, minimap_height = minimap_render_size

            ifopts.render.resolution.x = map_width
            ifopts.render.resolution.y = map_height
            ifopts.render.minimap_resolution.x = minimap_width
            ifopts.render.minimap_resolution.y = minimap_height

        if race is None:
            assert isinstance(observed_player_id, int), f"observed_player_id is of type {type(observed_player_id)}"
            request = sc_pb.RequestJoinGame(observed_player_id=observed_player_id, options=ifopts)
        else:
            assert isinstance(race, Race)
            request = sc_pb.RequestJoinGame(race=race.value, options=ifopts)

        if portconfig:
            request.server_ports.game_port = portconfig.server[0]
            request.server_ports.base_port = portconfig.server[1]

            for ppc in portconfig.players:
                port = request.client_ports.add()
                port.game_port = ppc[0]
                port.base_port = ppc[1]

        if name is not None:
            assert isinstance(name, str), f"name is of type {type(name)}"
            request.player_name = name

        result = await self._execute(join_game=request)
        self._game_result = None
        self._player_id = result.join_game.player_id
        return result.join_game.player_id

    patched_join_game._swarmbrain_interactive_join_patch = True
    Client.join_game = patched_join_game
