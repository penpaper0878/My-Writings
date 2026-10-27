#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DroneInspect Pro - UAV survey analytics & inspection-report generator for
civil / infrastructure assets:
  Bridges/ROB/flyovers, irrigation canals, EHV/HV transmission lines, solar PV
  plants, roads/pavements, buildings/facades, dams/embankments, above-ground
  pipelines, substations, plus any user-defined ("custom") asset profile.

INPUTS  (folder is scanned recursively; mixed media allowed)
  - RGB stills ........ .jpg .jpeg .png .tif .tiff .bmp .webp
  - Video ............. .mp4 .mov .avi .mkv .m4v  (frames sampled every N s;
                        DJI .SRT sidecar parsed for per-frame GPS/altitude)
  - Thermal ........... radiometric TIFF (float degC, or uint16 with scale/offset),
                        temperature matrix .csv/.txt,
                        DJI R-JPEG (via DJI Thermal SDK 'dji_irp' if configured),
                        otherwise palette JPEG/PNG/video + scale-bar Tmin/Tmax.
OUTPUTS (written to the chosen output folder)
  - Inspection_Report.html  (self-contained; print to PDF from any browser)
  - Inspection_Report.docx  (if python-docx installed)
  - findings.csv / findings.json / media_index.csv
  - annotated/  (overlay images: cracks red, corrosion orange, vegetation green,
                 spall/voids magenta, efflorescence cyan, thermal boxes)

INSTALL  : pip install numpy opencv-contrib-python pillow python-docx
           (opencv-contrib gives true thinning for crack skeletons; plain
            opencv-python works with a slower morphological fallback)
RUN GUI  : python drone_inspector.py        (or double-click DroneInspectPro.exe)
BUILD EXE: build_exe.bat on Windows, or the GitHub Actions workflow
           .github/workflows/build-windows-exe.yml (downloads as an artifact)
RUN CLI  : python drone_inspector.py --cli --asset bridge --input D:/survey --output D:/out
           python drone_inspector.py --list-assets
CUSTOMISE: Settings tab (JSON editor) or ~/.droneinspect/config.json. Every
           threshold, band, camera preset, priority/timeframe, knowledge-base
           text and asset profile is overridable; new asset profiles go under
           "custom_assets" (example "lattice_tower" shipped).

ANALYSIS ZONE: every frame is first segmented to the ASSET ITSELF (verified PV
           modules, concrete/steel structure, carriageway, canal prism, lattice
           tower, pipe, building envelope, switchyard). Detectors run only there
           (or in the asset's defined buffer), so neighbouring plots, fields,
           vehicles, roofs, soil, sky and water are never reported as defects.
           Frames where the asset is not found are skipped and listed. A manual
           mask  <image>_mask.png  or  masks/<image>.png  (white = asset)
           overrides the automatic zone for difficult scenes.
           Repeated sightings of one defect in overlapping frames are merged
           into one location group (counts & quantities not inflated).

LIMITATIONS: Detectors are classical computer-vision / radiometric screening
algorithms (no trained model is bundled; an ONNX/YOLO hook point is marked).
Every finding is a CANDIDATE needing verification by a competent inspector.
The report is decision support; it does not replace statutory inspection,
NDT, load rating or structural assessment.
"""

import argparse
import base64
import copy
import csv
import datetime as dt
import html
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import threading
import traceback
from dataclasses import dataclass, field, asdict
from pathlib import Path

# A windowed (no-console) Windows .exe has no stdout/stderr; give print() somewhere to go.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

try:
    import numpy as np
    import cv2
    from PIL import Image
except ImportError as _e:  # pragma: no cover
    print("Missing dependency:", _e,
          "\nInstall with:  pip install numpy opencv-contrib-python pillow python-docx")
    sys.exit(1)

try:
    import docx  # python-docx (optional)
    from docx.shared import Pt, Cm
    HAVE_DOCX = True
except Exception:
    HAVE_DOCX = False

APP_NAME = "DroneInspect Pro"
VERSION = "1.1.0"
CONFIG_PATH = Path.home() / ".droneinspect" / "config.json"
SEVERITIES = ["Low", "Medium", "High", "Critical"]
SEV_RANK = {s: i for i, s in enumerate(SEVERITIES)}
SEV_COLOR = {"Low": "#2e7d32", "Medium": "#f9a825", "High": "#e65100", "Critical": "#b71c1c"}

# =============================================================================
# 1. DEFAULT CONFIGURATION  (deep-merged with ~/.droneinspect/config.json)
#    Band lists use ">" semantics: [[x1,"Low"],[x2,"Medium"],...] means
#    value > x1 -> Low, value > x2 -> Medium ... (highest matching wins).
# =============================================================================
DEFAULT_CONFIG = {
    "report": {
        "company": "", "logo_path": "", "prepared_by": "", "reviewed_by": "", "approved_by": "",
        "max_register_rows": 400,          # findings shown with thumbnails
        "dedupe_radius_projected_m": 3.0,  # same defect seen in overlapping frames -> one location group
        "dedupe_radius_camera_m": 12.0,
        "include_media_appendix": True,
    },
    "processing": {
        "max_process_side_px": 4096,       # larger images are downscaled to this for CV (GSD rescaled); full res preserves fine cracks
        "video_frame_interval_s": 2.0,
        "video_dedupe_corr": 0.985,        # skip frames whose HSV-hist correlation > this
        "max_frames_per_video": 300,
        "blur_var_laplacian_min": 60.0,    # on 1000-px normalised image
        "exposure_dark_mean": 35, "exposure_bright_mean": 225,
        "skip_blurred": False,
        "save_annotated": True, "make_docx": True,
        "thumb_width_px": 420,
        "image_ext": [".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"],
        "video_ext": [".mp4", ".mov", ".avi", ".mkv", ".m4v"],
        "thermal_matrix_ext": [".csv", ".txt", ".raw"],
        # file is treated as thermal if path (upper-case) contains any token, or
        # resolution matches a thermal sensor format
        "thermal_name_tokens": ["_T.", "_T_", "THERM", "_IR.", "_IR_", "IR_", "_R.JPG", "RJPG", "/THERMAL", "\\THERMAL"],
        "thermal_resolutions": [[640, 512], [320, 256], [1280, 1024], [336, 256], [160, 120], [384, 288], [640, 480]],
        # hook for a trained detector (ONNX/YOLO) - not used by default
        "onnx_model_path": "",
    },
    # Asset region-of-interest: defects are only reported ON the asset (or its buffer zone)
    "roi": {
        "strict": True,                    # frames where the asset is not found are skipped (not guessed)
        "min_coverage_pct": 2.0,           # asset must cover >= this % of a frame to be analysed
        "manual_mask_suffixes": ["_mask.png", "_mask.jpg", ".mask.png"],   # white = asset (overrides auto)
        "mask_folder": "masks",            # or <data folder>/masks/<image stem>.png
        "grey_s_max": 60, "grey_v_min": 40, "grey_v_max": 245,
        "structure_min_pct": 3.0, "pavement_min_pct": 8.0,
        "core_erode_px": 0,                # 0 = auto (crack kernel/2 + 3): ignores asset boundary edges
        "woody_texture_std": 16.0, "woody_v_max": 150, "woody_min_area_m2": 2.0,
        "water_texture_std": 6.0, "water_min_pct": 1.0, "sky_texture_std": 2.5,
        "vehicle_exclusion": True, "vehicle_area_m2": [2.5, 30.0],
        "pv_min_rectangularity": 0.55, "pv_min_grid_score": 0.25, "pv_min_modules": 2, "pv_min_area_pct": 0.3,
        # module-scale repeat (m) when GSD is known: [min, max portrait pitch (module width + gap), max landscape pitch]
        # -> roof ribs / cell-scale texture (< min) and parked cars / parking bays (landscape-like pitch, no module rows) rejected
        "pv_pitch_m": [0.8, 1.45, 2.7],
        "pv_zone_close_m": 2.5, "pv_zone_close_pct": 3.0,   # bridges inter-row gaps (row pitch - table depth) only
        "pv_perimeter_m": 0.0,             # array zone = envelope of tables; land beyond the outer tables is NOT the plant
        "lattice_edge_density": 0.06,
        "pipe_diameter_m": [0.2, 2.0], "pipe_min_len_frac": 0.4, "pipe_max_interior_std": 25.0,
        "on_structure_ring_frac": 0.8,     # vegetation counts only if >= 80 % of its boundary is asset surface
        "framed_opening_min_side_m": 0.3,  # rectangular vegetation >= this (both sides) = land seen through an opening between members
        "straight_line_tortuosity": 1.03, "straight_line_min_frac": 0.2,  # joints/edges rejected as cracks
        "straight_band_min_frac": 0.1,     # straight dark bands >= 10 % of frame (interrupted joints) rejected as cracks
        "straight_band_min_px": 150,       # ... or >= 150 px (edge / shadow fragments cut by balusters, bars, markings)
        "thermal_border_pct": 2.0,         # uncooled-sensor vignetting margin ignored
        "thermal_pv_uniform_std_c": 0.8,
        "thermal_pv_table_min_rect": 0.75,
        "thermal_pv_row_max_dT": 10.0,     # row-completed table must be within 10 K of verified modules (open string ~ +3-8 K)  # module-grid-verified blocks must be table-like (not merged with ground)
        "thermal_equipment_max_pct": 12.0, # hot regions larger than this are sun-heated ground/roofs, not equipment
        "thermal_water_max_std_c": 0.3,
        "pair_visual_suffixes": ["_W", "_V"],   # DJI wide image paired with _T thermal for ROI transfer
    },
    "geometry": {
        "camera_preset": "auto",           # "auto" = match EXIF model + width
        "thermal_camera_preset": "auto",
        "gsd_override_mm_px": 0.0,         # >0 forces RGB GSD
        "thermal_gsd_override_mm_px": 0.0,
        "object_distance_m": 0.0,          # camera-to-surface distance for close-range/oblique work
        "use_gimbal_slant_range": True,    # alt / sin(|pitch|) when pitch shallower than nadir
    },
    # sensor_w_mm = active sensor width; verify against your datasheet.
    "camera_presets": {
        "DJI Phantom 4 RTK (FC6310R)": {"match": ["FC6310"], "sensor_w_mm": 13.2, "focal_mm": 8.8, "width_px": 5472, "thermal": False},
        "DJI Mavic 3 Enterprise wide (M3E)": {"match": ["M3E"], "sensor_w_mm": 17.3, "focal_mm": 12.29, "width_px": 5280, "thermal": False},
        "DJI Zenmuse P1 35 mm": {"match": ["ZENMUSEP1", "P1"], "sensor_w_mm": 35.9, "focal_mm": 35.0, "width_px": 8192, "thermal": False},
        "DJI Zenmuse H20T thermal": {"match": ["ZH20T"], "sensor_w_mm": 7.68, "focal_mm": 13.5, "width_px": 640, "thermal": True,
                                     "visual_fov_ratio": 0.42},   # thermal DFOV 40.6 / wide 82.9 deg - verify

        "DJI Matrice 30T thermal": {"match": ["M30T"], "sensor_w_mm": 7.68, "focal_mm": 9.1, "width_px": 640, "thermal": True,
                                    "visual_fov_ratio": 0.65},    # thermal DFOV 61 / wide 84 deg - verify
        "Generic 1-inch 20 MP": {"match": [], "sensor_w_mm": 13.2, "focal_mm": 8.8, "width_px": 5472, "thermal": False},
        "Custom camera (edit me)": {"match": [], "sensor_w_mm": 13.2, "focal_mm": 8.8, "width_px": 5472, "thermal": False},
    },
    "thermal": {
        "palette": "ironbow",              # ironbow | whitehot | blackhot | rainbow  (palette images only)
        "palette_tmin_c": 20.0, "palette_tmax_c": 60.0,   # read from the image scale bar
        "palette_crop_pct": [0, 0, 0, 0],  # top,bottom,left,right - remove burnt-in scale bar/text
        "tiff_uint16_scale": 0.01, "tiff_uint16_offset": -273.15,  # centikelvin default
        "dji_irp_path": "",                # e.g. C:/dji_thermal_sdk/utility/bin/windows/release_x64/dji_irp.exe
        "dji_irp_extra_args": [],          # e.g. ["--emissivity","0.95","--distance","10"]
        "smoothing_sigma": 0.8,
        "min_anomaly_px": 6,
        "local_bg_fraction": 0.08,         # Gaussian sigma for local background = fraction of max(h,w)
        "electrical_fg_delta_c": 3.0,      # pixels this much above image median = equipment/conductor
        "electrical_min_report_c": 2.0,
        "civil_noise_k": 3.5,              # civil/seepage/building/pipeline: anomaly > 3.5 x surface thermal spread
        "civil_edge_grad_c_px": 0.5,       # gradients steeper than this (C/px) are member edges, not surface texture
        "civil_max_anomaly_pct": 4.0,      # warm regions larger than this % of the frame = sun/shadow loading, not delamination
        "pv_cell_min_dT": 5.0,             # cell-sized PV anomalies below this (K) are not reported
        "pv_noise_k": 4.0,                 # ... nor below 4 x the array's module-to-module spread
    },
    "environment": {                       # entered in GUI; used for QA & corrections
        "ambient_temp_c": None, "irradiance_wm2": None, "wind_speed_ms": None,
        "cloud_okta": None, "humidity_pct": None, "load_pct": None,
        "load_exponent": 2.0,              # dT_corr = dT * (100/load%)^n
    },
    "thresholds": {
        "crack": {
            "blackhat_kernel_px": 17, "min_contrast": 12, "min_length_px": 45,
            "max_mean_width_px": 14, "min_elongation": 6.0, "sigma_k": 3.0, "max_junctions_per_px": 0.08,
            # concrete: Low <0.10 <= Medium <0.30 <= High <1.0 <= Critical  (mm)
            "width_bands_mm": [[0.0, "Low"], [0.10, "Medium"], [0.30, "High"], [1.0, "Critical"]],
            # pavements (ASTM D6433 L/M/H at 10 / 76 mm; <3 mm hairline)
            "pavement_width_bands_mm": [[0.0, "Low"], [3.0, "Medium"], [10.0, "High"], [76.0, "Critical"]],
            "map_pattern_junctions_per_100px": 1.2,
            "max_crack_width_mm": 15.0,    # wider linear features are joints / gaps / edges, not cracks
            "crack_void_darkness": 0.6,    # relative darkness of an open crack void (photometric width of sub-pixel cracks)
        },
        "corrosion": {"hsv_lo": [3, 90, 40], "hsv_hi": [22, 255, 210], "min_area_pct": 0.05,
                      # ISO 4628-3 Ri grades: rusted area %
                      "ri_limits_pct": [0.05, 0.5, 1.0, 8.0, 40.0],
                      "ri_severity": ["Low", "Low", "Low", "Medium", "High", "Critical"]},
        "efflorescence": {"s_max": 28, "v_min": 205, "bands_pct": [[0.0, "Low"], [2.0, "Medium"], [6.0, "High"]],   # any deposit = Low
                          "min_area_m2": 0.002},
        "vegetation": {"exg_min": 0.06, "bands_pct": [[1.0, "Low"], [5.0, "Medium"], [15.0, "High"], [35.0, "Critical"]],
                       # any growth ON a structure is a defect: % of asset surface in frame
                       "on_structure_bands_pct": [[0.0, "Low"], [1.0, "Medium"], [5.0, "High"]],
                       # any weed inside a PV array / switchyard / pipe RoW zone is a maintenance item: % of zone
                       "zone_bands_pct": [[0.0, "Low"], [3.0, "Medium"], [10.0, "High"], [25.0, "Critical"]],
                       "min_plant_m2": 0.03},          # smallest plant reported (noise / moss specks below)
        # module glass fracture lines (~1-3 mm) are only resolvable on close-range imagery
        "pv_glass": {"max_gsd_mm": 10.0, "min_off_axis_frac": 0.35},
        "spall": {"local_drop": 38, "ring_drop": 25, "min_area_pct": 0.03, "min_area_m2": 0.01, "max_area_pct": 12.0, "max_solidity": 0.95, "max_rectangularity": 0.85,
                  "min_thickness_m": 0.04,
                  "bands_pct": [[0.0, "Medium"], [1.0, "High"], [4.0, "Critical"]]},
        "moisture": {"drop": 30, "s_max": 120, "min_area_m2": 0.5, "min_area_pct": 0.3,
                     "bands_pct": [[0.0, "Low"], [5.0, "Medium"], [15.0, "High"]]},   # any confirmed wet patch = Low
        "soiling": {"bands_pct": [[0.0, "Low"], [5.0, "Medium"], [15.0, "High"]],   # % of verified module area; any patch = Low
                    "dl_min": 8.0, "dl_sigma": 3.0,   # brighter than clean modules by >= max(8 L*, 3 sigma)
                    "min_blob_m": 0.10,               # deposit must be wider than a frame + gap band
                    "min_area_m2": 0.05,              # smallest reportable deposit area (~22 cm patch)
                    "close_gsd_mm": 10.0, "min_blob_close_m": 0.06, "min_area_close_m2": 0.004,   # close-range limits
                    "max_aspect": 2.2,                # deposits are compact; edge streaks / frame reflections are not
                    "min_clean_ring_frac": 0.5},      # >= 50 % of its surroundings must be clean module glass
        # NETA MTS/ATS Table 100.18
        "thermal_electrical_c2c": [[0.99, "Low"], [3.0, "Medium"], [15.0, "Critical"]],
        "thermal_electrical_amb": [[0.99, "Low"], [10.0, "Medium"], [20.0, "High"], [40.0, "Critical"]],
        # PV: common O&M defaults; IEC TS 62446-3 classes CoA1/2/3 by abnormality type - align to contract
        "thermal_pv": [[3.0, "Low"], [10.0, "Medium"], [20.0, "High"], [40.0, "Critical"]],
        "thermal_civil_warm": [[0.5, "Low"], [1.5, "Medium"], [3.0, "High"], [6.0, "Critical"]],
        "thermal_seepage_cold": [[1.0, "Low"], [2.0, "Medium"], [4.0, "High"], [6.0, "Critical"]],
        "thermal_building": [[2.0, "Low"], [4.0, "Medium"], [8.0, "High"], [15.0, "Critical"]],
        "thermal_pipeline": [[3.0, "Low"], [8.0, "Medium"], [15.0, "High"], [30.0, "Critical"]],
        "thermal_area_escalate_pct": 5.0,  # civil anomalies larger than this (% of frame) escalate one level
    },
    "pv": {
        "module_length_m": 2.278, "module_width_m": 1.134, "module_wp": 550,
        "modules_per_string": 28, "specific_yield_kwh_per_kwp_day": 4.5, "tariff_per_kwh": 3.0,
        "currency": "INR",
        # fraction of module power lost per thermal pattern (indicative)
        "loss_fraction": {"cell": 0.05, "substring": 0.33, "module": 1.0, "multi_module": 1.0, "string": 1.0},
        "min_irradiance_wm2": 600,         # IEC TS 62446-3 minimum for thermography
    },
    "ht_line": {
        "voltage_kv": 220,
        # MoP (GoI) RoW guideline 2015, m (verify against current order)
        "row_width_m": {"66": 18, "110": 22, "132": 27, "220": 35, "400": 46, "765": 64,
                        "500 HVDC": 52, "800 HVDC": 69},
    },
    "severity_weights": {"Low": 1, "Medium": 3, "High": 8, "Critical": 20},
    "priority": {
        "Critical": ["P1", "Immediate - within 7 days; impose interim safety measures (isolation / load or speed restriction / barricading) now"],
        "High": ["P2", "Urgent - within 30 days"],
        "Medium": ["P3", "Planned - within 90 days / next maintenance window"],
        "Low": ["P4", "Routine - monitor; address at next periodic maintenance"],
    },
    "knowledge_base_overrides": {},        # {"defect_key": {"solution": [...], ...}}
    "custom_assets": {
        "lattice_tower": {
            "label": "Telecom / Lattice Steel Tower (custom example)",
            "roi": "tower", "veg_rule": "zone_woody", "zone_independent": True, "max_process_side_px": 2400,   # roi: pv|structure|yard|pavement|canal|tower|linear|facade|full
            "detectors": ["quality", "corrosion", "vegetation"],
            "thermal_mode": "electrical",
            "map": {"corrosion": "corrosion_steel", "vegetation": "vegetation_row"},
            "components": ["Legs", "Bracings", "Platforms", "Antenna mounts", "Foundations/chimney", "Earthing"],
            "manual_checklist": ["Verticality / twist (total station)", "Bolt tightness & missing bolts",
                                 "Earthing resistance", "Aviation obstruction lights"],
            "frequency": "Annual visual + post-cyclone inspection",
            "references": ["TIA-222-H", "IS 802 (Part 1/Sec 1 & 2)", "ISO 12944 (corrosion protection)"],
        }
    },
}

# =============================================================================
# 2. KNOWLEDGE BASE - defect diagnosis, remedies, improvements, actions
# =============================================================================
KB = {
    "crack_concrete": {
        "name": "Cracking in concrete member (RCC/PSC)",
        "description": "Linear discontinuities detected by morphological black-hat + skeletonisation; width from distance transform x GSD. Pattern aids diagnosis: diagonal near supports -> shear; vertical at mid-span soffit -> flexure; longitudinal along bar line -> corrosion-induced splitting; map/random -> plastic/drying shrinkage, ASR or DEF.",
        "causes": ["Flexural / shear overstress (overloading, loss of prestress, inadequate reinforcement)",
                   "Restrained shrinkage & thermal gradients", "Reinforcement corrosion (expansive rust, ~2-6x steel volume)",
                   "Alkali-silica reaction / delayed ettringite formation", "Differential settlement, bearing seizure, impact"],
        "consequences": ["Ingress of chlorides/CO2/moisture -> depassivation & corrosion", "Loss of stiffness and durability",
                         "In PSC: cracking under service load indicates decompression / prestress loss - structural concern"],
        "solution": ["<0.1 mm dormant: hydrophobic impregnation (silane/siloxane) or elastomeric anti-carbonation coating",
                     "0.1-~6 mm dormant structural: pressure injection, low-viscosity epoxy (ASTM C881 Type IV, Grade 1), after flushing",
                     "Active / leaking cracks: hydrophilic polyurethane resin injection; rout-and-seal with flexible sealant where movement continues",
                     "Wide / structural cracks: stitching, section enlargement (jacketing), externally bonded FRP (ACI 440.2R) or external prestressing per design",
                     "ASR/DEF map cracking: moisture control (coatings, drainage), monitoring expansion, strengthening if required"],
        "improvement": ["Improve deck drainage & waterproofing; seal expansion joints", "Apply anti-carbonation / chloride-barrier coating after repair",
                        "Install crack monitors (tell-tales, vibrating-wire gauges) on critical cracks", "Review load-posting / overloading control"],
        "actions": ["Map all cracks (location, length, width, orientation) on member drawings", "Determine active vs dormant over thermal/load cycles (crack-width gauges 3-6 months)",
                    "Cover meter survey, carbonation depth (phenolphthalein), chloride profile (ASTM C1152/C1218)",
                    "Half-cell potential ASTM C876 (< -350 mV CSE: >90 % corrosion probability; -200 to -350 mV uncertain; > -200 mV: <10 %)",
                    "Structural assessment / load rating if shear or flexural cracks exceed code limits or any crack in PSC zone under service load"],
        "ndt": ["Rebound hammer IS 516 Pt5/IS 13311-2", "UPV IS 13311-1 (>4.5 km/s excellent, 3.5-4.5 good, 3.0-3.5 medium, <3.0 doubtful)", "Cores IS 516", "GPR"],
        "references": ["IRC:SP:35 & IRC:SP:52 (bridge inspection)", "IRC:SP:40 (repair/rehabilitation)", "IRC:112-2020 crack-width limits",
                       "IS 456:2000 cl. 35.3.2", "ACI 224R-01 / 224.1R-07", "EN 1504 (repair products & principles)"],
    },
    "crack_lining": {
        "name": "Cracking / panel distress in canal lining",
        "description": "Cracks in cement-concrete lining panels detected from RGB imagery; longitudinal cracks at slope toe and transverse cracks between contraction joints are typical.",
        "causes": ["Uplift / hydrostatic back-pressure on rapid drawdown (inadequate pressure relief)", "Expansive (BC) sub-grade swelling & shrinkage",
                   "Settlement of poorly compacted embankment fill", "Missing/failed contraction joints, thermal movement", "Root penetration, rodent burrows"],
        "consequences": ["Seepage losses & waterlogging of command area", "Sub-grade erosion -> panel collapse / slips", "Increased roughness (Manning n) and reduced capacity"],
        "solution": ["Hairline/dormant: seal with polymer-modified slurry or bitumen sealing compound (IS 1834)",
                     "Open cracks: cut V-groove, clean, fill with polysulphide (IS 12118) / PU sealant", "Displaced/broken panels: demolish, restore & compact sub-grade (CNS layer on expansive soil), relay M15/M20 PCC with joints",
                     "Provide pressure-relief valves / longitudinal & cross drains under lining where groundwater/back-pressure exists"],
        "improvement": ["Geomembrane (HDPE/LLDPE) below lining for high-loss reaches", "CNS (cohesive non-swelling) cushion on expansive soils",
                        "Controlled drawdown rate in operations", "Pre-monsoon joint resealing programme"],
        "actions": ["Chainage-wise defect inventory", "Ponding/inflow-outflow seepage test on suspect reach", "Check subgrade (FDD/field density, swell index)"],
        "ndt": ["Ponding test", "Inflow-outflow test", "GPR for voids below lining"],
        "references": ["IS 3873 (laying cement-concrete lining)", "IS 10430 (lined canal design)", "IS 5331 (selection of lining)", "IS 9451 (lining on expansive soils)", "CWC guidelines"],
    },
    "crack_pavement": {
        "name": "Pavement cracking (longitudinal / transverse / alligator)",
        "description": "Cracks in bituminous / rigid pavement. Map pattern with high junction density is classified as alligator (fatigue) cracking.",
        "causes": ["Fatigue of bituminous layer under repeated axle loads (alligator)", "Thermal / reflective cracking from underlying CTB/old PCC",
                   "Weak or saturated subgrade, poor drainage", "Ageing / oxidation of binder", "Construction joint failure"],
        "consequences": ["Water ingress -> base weakening -> potholes", "Loss of riding quality (IRI increase)", "Accelerated structural failure"],
        "solution": ["<3 mm: fog seal / slurry seal / micro-surfacing", "3-10 mm: crack sealing with bitumen emulsion or hot-poured rubberised sealant",
                     ">10 mm / alligator: remove distressed area to sound layer, patch with DBM/BC (full-depth if base failed)",
                     "Extensive: overlay design by BBD/FWD (IRC:81 / IRC:115) or recycling"],
        "improvement": ["Restore camber and side drains", "Paved shoulders", "Use modified binders (PMB/CRMB) in overlay", "Periodic preventive maintenance cycle"],
        "actions": ["Network-level distress survey (PCI per ASTM D6433)", "FWD/BBD deflection testing", "Roughness (IRI) survey", "Check subgrade CBR & moisture"],
        "ndt": ["FWD", "BBD", "GPR for layer thickness", "Network survey vehicle"],
        "references": ["IRC:82-2015 (maintenance of bituminous roads)", "IRC:81 / IRC:115 (overlay design)", "ASTM D6433 (PCI)", "IRC:SP:83 (rigid pavements maintenance)"],
    },
    "spalling": {
        "name": "Spalling / delamination / honeycomb / void (candidate)",
        "description": "Locally dark, irregular, non-linear regions relative to surrounding surface (local-contrast segmentation, solidity filter). Typical of spalled cover with exposed reinforcement, honeycombing or voids. Shadows can create false positives - verify.",
        "causes": ["Reinforcement corrosion pressure (carbonation / chlorides)", "Insufficient cover", "Poor compaction (honeycomb)", "Freeze-thaw, fire, impact"],
        "consequences": ["Loss of section and bond", "Accelerated corrosion of exposed bars", "Falling-debris hazard to traffic/public"],
        "solution": ["Remove loose concrete; break out to sound concrete and >= 20-25 mm behind exposed bars",
                     "Clean reinforcement to Sa 2.5 / St 3; replace/lap bars with >10-15 % section loss (per design)",
                     "Apply zinc-rich / alkaline anti-corrosion primer and bonding agent",
                     "Reinstate with polymer-modified mortar (thin) or free-flow micro-concrete (deep) - EN 1504-3 R3/R4 class",
                     "Consider galvanic anodes / impressed-current cathodic protection for chloride-contaminated members"],
        "improvement": ["Protective anti-carbonation coating", "Migrating corrosion inhibitors", "Fix water leakage source (drains, joints)"],
        "actions": ["Sounding (hammer tap / chain drag) to delineate delaminated area", "Barricade / net areas above traffic or public access",
                    "Cover meter, carbonation, chloride and half-cell tests in surrounding area"],
        "ndt": ["Hammer sounding", "IR thermography (ASTM D4788)", "GPR", "Impact-echo"],
        "references": ["IRC:SP:40", "EN 1504-3 / EN 1504-9", "ACI 546R (repair guide)", "ASTM D4580 (sounding)"],
    },
    "corrosion_steel": {
        "name": "Corrosion of steel members / coating breakdown",
        "description": "Rust-coloured area segmented in HSV on the delineated steel member/structure only (soil, roofs, vehicles excluded); graded to ISO 4628-3 rust scale Ri0-Ri5 (0, 0.05, 0.5, 1, 8, 40-50 % rusted area) as % of the visible asset surface.",
        "causes": ["Coating ageing / UV degradation", "Water & debris traps at connections, bird droppings", "Galvanic coupling, crevices at bolted joints",
                   "Aggressive (C4/C5) atmosphere: industrial, coastal"],
        "consequences": ["Section loss -> reduced member capacity", "Bolt/connection failure", "Pack rust distorting plates"],
        "solution": ["Ri1-Ri2: spot-repair - St 2/St 3 hand/power tool cleaning, touch-up compatible primer + finish",
                     "Ri3-Ri4: abrasive blast Sa 2.5 locally / fully, repaint system per ISO 12944 durability class",
                     "Ri5 / measurable section loss: UT thickness survey, replace or plate members per design check, then full repainting",
                     "Galvanized towers: zinc-rich paint (>= 80 % Zn in dry film) on rusted areas"],
        "improvement": ["Select coating system to ISO 12944-5 for site corrosivity category", "Detail drainage holes; seal crevices; bird deterrents",
                        "Cathodic protection for buried / immersed parts"],
        "actions": ["Measure DFT, adhesion (ISO 4624 pull-off / cross-cut ISO 2409)", "UT thickness gauging at worst locations",
                    "Check bolts (missing, loose, corroded) and replace"],
        "ndt": ["UT thickness", "DFT gauge", "MPI/DPT at welds"],
        "references": ["ISO 4628-3", "ISO 8501-1 (surface prep grades)", "ISO 12944 (1-9)", "IS 800 / IS 802", "IRC:24 (steel bridges)"],
    },
    "corrosion_concrete": {
        "name": "Rust staining - reinforcement corrosion indication",
        "description": "Rust-coloured staining on concrete surface (HSV segmentation) indicates active reinforcement corrosion beneath, often along bar lines or at cracks.",
        "causes": ["Carbonation reaching bar depth", "Chloride contamination (de-icing salt, marine, admixtures)", "Low cover / porous concrete", "Leaking joints & drains"],
        "consequences": ["Cracking & spalling", "Rebar section loss, loss of bond & ductility", "Prestressing tendon corrosion (brittle failure risk in PSC)"],
        "solution": ["Expose and assess bars at stained locations; treat as spalling repair",
                     "Chloride extraction / re-alkalisation for widespread contamination", "Cathodic protection (galvanic/ICCP)",
                     "Surface-applied corrosion inhibitor + protective coating"],
        "improvement": ["Stop water ingress (drainage spouts, joint seals, deck waterproofing)", "Increase cover in reconstruction (IRC:112 / IS 456 exposure class)"],
        "actions": ["Half-cell potential mapping ASTM C876", "Chloride (ASTM C1152/C1218) & carbonation profiles", "Resistivity (Wenner)", "For PSC: tendon inspection (borescope at anchorages)"],
        "ndt": ["Half-cell", "Resistivity", "Cover meter", "GPR"],
        "references": ["ASTM C876", "IRC:SP:40", "IS 456 Table 3/16", "EN 1504-9"],
    },
    "efflorescence": {
        "name": "Efflorescence / leaching / calcite deposits",
        "description": "Bright, low-saturation deposits on concrete/masonry (HSV), typical at cracks, construction joints, deck soffit and drainage outlets.",
        "causes": ["Water percolating through cracks/joints dissolving Ca(OH)2", "Failed deck waterproofing / joints", "Porous concrete / masonry"],
        "consequences": ["Increased porosity & strength loss (leaching)", "Indicator of water path to reinforcement", "Aesthetic deterioration"],
        "solution": ["Remove deposits (mild acid wash / mechanical), then seal water path: crack injection (PU if wet), joint resealing",
                     "Repair / provide deck waterproofing membrane & drainage", "Apply breathable hydrophobic treatment"],
        "improvement": ["Drip mouldings, extended drainage spouts", "Regular cleaning of drains & weep holes"],
        "actions": ["Trace source of water (above-deck inspection after rain)", "Check for associated cracking & corrosion"],
        "ndt": ["Moisture meter", "IR thermography"],
        "references": ["IRC:SP:35", "IRC:SP:40", "ACI 201.1R (condition survey)"],
    },
    "vegetation_structure": {
        "name": "Vegetation growth on structure",
        "description": "Vegetation detected (Excess-Green index) on/near structural elements - joints, bearings, abutments, parapets, drainage.",
        "causes": ["Accumulated soil/debris in joints and drains", "Persistent moisture / leakage", "Lack of routine maintenance"],
        "consequences": ["Root wedging enlarges cracks/joints", "Moisture retention -> corrosion", "Obstructs inspection & drainage; fire risk"],
        "solution": ["Uproot (do not merely cut) including roots; treat with approved systemic herbicide",
                     "Clean and seal joints/cracks after removal", "Clear drainage outlets"],
        "improvement": ["Include vegetation removal in pre- and post-monsoon maintenance", "Fix leakage that sustains growth"],
        "actions": ["Inspect for root-induced cracking after removal"],
        "ndt": [], "references": ["IRC:SP:35", "IRC:SP:18 (highway maintenance)"],
    },
    "vegetation_canal": {
        "name": "Weed / vegetation in canal prism, berms or service road",
        "description": "Vegetation cover (ExG index) on bed, slopes, berms or embankment.",
        "causes": ["Silt deposition providing growth medium", "Cracked/missing lining", "Low velocity / stagnant reaches", "Deferred maintenance"],
        "consequences": ["Increased roughness (Manning n may double) -> reduced discharge, tail-end deprivation",
                         "Root damage to lining & embankment, seepage paths", "Obstructs inspection & patrolling"],
        "solution": ["Mechanical removal (manual / excavator with weed-cutting bucket) before irrigation season",
                     "Desilting to design bed level & section", "Approved herbicide application per local regulations (keep out of water)",
                     "Repair lining after removal"],
        "improvement": ["Annual maintenance calendar (closure period)", "Maintain design velocity (non-silting, non-scouring)", "Grass turfing only on outer embankment slopes"],
        "actions": ["Chainage-wise cross-section survey vs design", "Estimate conveyance loss / capacity (Manning) with measured n"],
        "ndt": [], "references": ["IS 10430", "State Irrigation Department maintenance manuals", "CWC guidelines"],
    },
    "vegetation_row": {
        "name": "Vegetation encroachment in Right-of-Way / corridor",
        "description": "Woody vegetation (tree/shrub canopy, textured, >= 2 m2) in the corridor; crops, grass and turf are not counted. Height/clearance to conductor cannot be measured from single 2D images - confirm with LiDAR or photogrammetric DSM.",
        "causes": ["Tree growth below / beside conductors", "Inadequate RoW clearing cycle"],
        "consequences": ["Flashover / tripping, ground-fault, forest fire", "Violation of statutory electrical clearance", "Access obstruction for maintenance"],
        "solution": ["Trim / fell trees infringing clearance with permits (forest/landowner)", "Corridor clearing to RoW width; maintain minimum clearance",
                     "Selective herbicide for re-growth control where permitted"],
        "improvement": ["LiDAR corridor survey with vegetation-to-conductor clearance classification at max sag (max conductor temperature)",
                        "Risk-based vegetation management cycle"],
        "actions": ["Measure tree-to-conductor clearance (LiDAR / hot-stick rangefinder)", "Prioritise spans near clearance limit before summer peak load"],
        "ndt": ["LiDAR"], "references": ["CEA (Measures relating to Safety & Electric Supply) Regulations, 2023", "MoP RoW Guidelines (2015)", "IS 5613"],
    },
    "vegetation_solar": {
        "name": "Vegetation / shading hazard in PV array",
        "description": "Vegetation inside the PV array envelope (between rows and under/over tables). Land beyond the outermost tables - neighbouring plots, roads, fenced strips - is not counted.",
        "causes": ["Inadequate grass-cutting cycle", "Wind-blown seeds, high rainfall"],
        "consequences": ["Partial shading -> hot-spots, bypass-diode activation, mismatch loss", "Fire hazard near DC cables", "Cable damage by rodents"],
        "solution": ["Mechanical grass cutting below module lower edge", "Pre-emergent herbicide / gravel or anti-weed membrane where permitted"],
        "improvement": ["Vegetation management schedule in O&M contract with height limit", "Consider grazing (agri-PV) where compatible"],
        "actions": ["Correlate with thermal hot-spots at lower module edges"], "ndt": [],
        "references": ["IEC 62446-1", "IEC TS 62446-3"],
    },
    "moisture": {
        "name": "Wet patch / moisture / waterlogging (candidate)",
        "description": "Dark, low-saturation regions consistent with wet surfaces or ponding. Shadows and dark materials give false positives - low confidence; corroborate with thermal (cold anomaly) data.",
        "causes": ["Seepage through embankment / lining", "Blocked drains, inadequate camber", "Leaking pipes / joints"],
        "consequences": ["Softening of soil, piping & slope instability", "Corrosion of foundations", "Loss of access"],
        "solution": ["Locate source; provide drainage / toe drain / filter", "Repair lining / joints / pipelines upstream"],
        "improvement": ["Surface drainage design check", "Piezometers in embankments"],
        "actions": ["Ground verification after dry spell", "Thermal survey early morning for seepage"], "ndt": ["IR thermography", "Piezometers"],
        "references": ["CWC Guidelines for Safety Inspection of Dams", "IS 8237 (embankment protection)"],
    },
    "pothole": {
        "name": "Pothole / surface ravelling (candidate)",
        "description": "Dark, irregular depressions in pavement surface (local-contrast segmentation). Depth not measurable from 2D image.",
        "causes": ["Water ingress through cracks", "Inadequate binder content / stripping", "Weak base, heavy loads"],
        "consequences": ["Safety hazard (two-wheelers)", "Rapid propagation in monsoon"],
        "solution": ["Cut rectangular to sound material, clean, tack coat, fill with hot-mix (BM/DBM+BC) or cold mix, compact in layers",
                     "Mechanised pothole repair (jet-patcher) for quick fixes"],
        "improvement": ["Crack sealing programme to prevent pothole formation", "Drainage improvement"],
        "actions": ["Measure depth/area on site", "Fix within statutory response time (contract KPIs)"], "ndt": [],
        "references": ["IRC:82-2015", "IRC:SP:100 (cold mix)", "ASTM D6433"],
    },
    "soiling_pv": {
        "name": "Module soiling (dust / bird droppings)",
        "description": "Deposits clearly brighter than the clean glass of the same table (local module-scale reference, any hue), after removing frames, inter-module gaps and cell grid; must be surrounded mostly by clean glass. Reported as % of verified module area.",
        "causes": ["Dust from nearby roads/agriculture", "Bird droppings", "Low tilt / infrequent cleaning", "Dew-cementation of dust"],
        "consequences": ["Energy loss (often 1-7 %/month in arid sites)", "Bird droppings cause localised hot-spots"],
        "solution": ["Water-based cleaning with soft brush (low TDS water) or robotic dry cleaning", "Spot-clean bird droppings"],
        "improvement": ["Soiling-ratio station / reference cells to optimise cleaning frequency", "Bird deterrents", "Anti-soiling coatings"],
        "actions": ["Compare string currents/PR before-after cleaning"], "ndt": ["IV-curve tracing"],
        "references": ["IEC 61724-1 (soiling ratio)", "IEC 62446-1"],
    },
    "pv_glass_damage": {
        "name": "Broken glass / cracked module (candidate)",
        "description": "Bright, branched linear patterns inside module area (shattered glass, snail trails, micro-crack signatures).",
        "causes": ["Impact (hail, stones, handling)", "Mechanical stress during installation / cleaning", "Frame / mounting distortion"],
        "consequences": ["Moisture ingress, insulation failure (ground fault)", "Hot-spots, power loss", "Electric-shock hazard"],
        "solution": ["Isolate string; replace module with electrically-matched spare", "Insulation resistance test of string after replacement"],
        "improvement": ["Train cleaning crew (no walking on modules)", "Hail-guard procedures (stow for trackers)"],
        "actions": ["EL imaging / IV curve to confirm", "Warranty / insurance claim documentation"], "ndt": ["Electroluminescence", "IV curve", "Insulation resistance (IEC 62446-1)"],
        "references": ["IEC 62446-1", "IEC 61215", "IEC 61730"],
    },
    "thermal_electrical": {
        "name": "Thermal anomaly - electrical connection / component overheating",
        "description": "Hot-spot relative to similar components (component-to-component dT) and, when ambient is entered, over ambient. Severity per NETA MTS Table 100.18: C2C 1-3 C possible deficiency, 4-15 C probable deficiency, >15 C major (repair immediately); over-ambient 1-10 / 11-20 / 21-40 / >40 C. Optional load correction dT x (100/load%)^n.",
        "causes": ["Loose / corroded bolted connections, jumpers, PG clamps, mid-span joints", "Improper compression of joints (dead-end, mid-span)",
                   "Broken strands, overloaded conductor", "Insulator leakage current / internal defect (polymer)", "Unbalanced load, failing breaker/isolator contacts"],
        "consequences": ["Accelerated oxidation -> thermal runaway -> conductor drop / open circuit", "Unplanned outage, fire risk", "Annealing of conductor (loss of strength)"],
        "solution": ["Planned shutdown; dismantle joint, clean contact surfaces (wire brush), apply contact grease, re-torque to manufacturer values",
                     "Replace damaged clamps / joints / jumpers; re-compress or replace mid-span joints (implosive / compression)",
                     "Replace defective insulators (polymer hot-spot = internal tracking)", "Re-balance load / review conductor ampacity"],
        "improvement": ["Periodic thermography (at >= 40 % rated load) and trending", "Bi-metallic connectors at Al-Cu junctions", "Torque-controlled installation and records"],
        "actions": ["Repeat scan at known load; record load, emissivity, distance, ambient, wind", "Contact resistance (micro-ohm) measurement after repair", "Re-scan within 1 month after repair"],
        "ndt": ["Micro-ohm meter", "Corona (UV) camera", "Hot-line washing / insulator testing"],
        "references": ["NETA MTS-2023 / ATS Table 100.18", "IEEE Std 524 (installation)", "IS 5613", "CEA Safety Regulations 2023", "ISO 18434-1 (thermography condition monitoring)"],
    },
    "thermal_pv": {
        "name": "Thermal abnormality in PV array",
        "description": "Anomaly relative to robust array reference temperature, pattern-classified by area vs module footprint (thermal GSD): cell hot-spot, substring (bypass-diode), whole module (open circuit / short-circuited module), multi-module patchwork (PID-like) or string offline. Classes per IEC TS 62446-3 (CoA1 no abnormality, CoA2 thermal abnormality, CoA3 safety-relevant).",
        "causes": ["Cell crack / defective cell / shading / soiling (cell hot-spot)", "Bypass diode short or open (substring ~1/3 module)",
                   "Disconnected / failed module or J-box (whole module)", "Potential-induced degradation (patchwork near negative string end)",
                   "Blown fuse / open connector / inverter MPPT off (string offline)", "Delamination, J-box overheating"],
        "consequences": ["Energy loss (see estimate)", "Backsheet burn / fire risk at high dT (CoA3)", "Accelerated degradation, warranty implications"],
        "solution": ["Cell hot-spot: clean / remove shading; if persistent with dT > ~20 K replace module",
                     "Substring: test & replace bypass diode or module", "Module open circuit: check MC4 connectors / J-box; replace module",
                     "String offline: check string fuse, DC cable, connectors, SCB/inverter MPPT channel", "PID: PID recovery box / negative-ground; replace badly affected modules"],
        "improvement": ["Annual IR + IV-curve campaigns", "SCADA string-level monitoring with alarms", "Connector crimping QA (same manufacturer pairs)"],
        "actions": ["Verify irradiance >= 600 W/m2 and stable during scan (IEC TS 62446-3)", "IV-curve trace affected strings; EL imaging for suspect modules",
                    "Prepare module-level defect list with row/table/string IDs for O&M"],
        "ndt": ["IV-curve tracer", "EL imaging", "Insulation resistance"],
        "references": ["IEC TS 62446-3:2017", "IEC 62446-1", "IEC 61215", "IEC TS 62804-1 (PID)"],
    },
    "thermal_civil_warm": {
        "name": "Thermal anomaly - subsurface delamination / void (warm)",
        "description": "Under solar loading (daytime) delaminations/voids appear warmer than sound concrete (and cooler at night). Local anomaly vs smoothed background.",
        "causes": ["Corrosion-induced delamination", "Debonded overlay / wearing coat", "Voids, honeycomb, debonded repairs"],
        "consequences": ["Future spalling", "Hidden loss of bond & section"],
        "solution": ["Delineate by sounding; remove and repair as spalling", "Overlay replacement where debonded"],
        "improvement": ["Include IR + GPR deck survey in periodic inspection"],
        "actions": ["Verify by hammer sounding / chain drag / cores", "Scan with adequate solar loading; record time, ambient, wind"],
        "ndt": ["ASTM D4788 IR", "GPR", "Impact echo", "Sounding ASTM D4580"],
        "references": ["ASTM D4788", "ASTM D4580", "IRC:SP:35"],
    },
    "thermal_seepage_cold": {
        "name": "Thermal anomaly - seepage / moisture (cold)",
        "description": "Evaporative cooling makes seepage zones cooler than dry soil/concrete (best in early morning or late afternoon). Local cold anomaly vs smoothed background.",
        "causes": ["Seepage through embankment / foundation / lining", "Piping paths, rodent holes", "Leaking joints, internal erosion"],
        "consequences": ["Internal erosion & piping -> breach", "Slope saturation & sloughing", "Water loss"],
        "solution": ["Inverted filter / toe drain / relief wells at exit points", "Cut-off (clay / sheet pile / slurry wall) or upstream impervious blanket",
                     "Reline the reach (geomembrane) in canals", "Grouting of voids where appropriate"],
        "improvement": ["Piezometer & V-notch seepage measurement programme", "Vegetation control to keep exits visible"],
        "actions": ["Ground-truth: turbidity of seep (fines = active piping -> EMERGENCY)", "Measure seepage flow; monitor trend", "Inform dam-safety organisation if on a dam"],
        "ndt": ["Piezometers", "Electrical resistivity tomography", "V-notch weirs"],
        "references": ["CWC Guidelines for Safety Inspection of Dams (2018)", "Dam Safety Act 2021 (India)", "USBR / FEMA embankment guidelines"],
    },
    "thermal_building": {
        "name": "Thermal anomaly - building envelope (heat loss / moisture / thermal bridge)",
        "description": "Local warm/cold anomaly on facade/roof. Interpret with interior-exterior temperature difference (ideally >= 10 K) and time of day.",
        "causes": ["Missing / wet insulation", "Thermal bridges at slab edges / lintels", "Air leakage at openings", "Water ingress in roof/facade"],
        "consequences": ["Energy loss, condensation & mould", "Deterioration of finishes / corrosion of fixings"],
        "solution": ["Moisture: trace leak, repair waterproofing, dry out", "Insulation: re-insulate / inject cavity", "Seal air leaks; repair joints & glazing"],
        "improvement": ["Blower-door test", "Envelope retrofit design"], "actions": ["Confirm moisture with meter / cores"], "ndt": ["Moisture meter", "Blower-door"],
        "references": ["ISO 6781-1:2023", "ASTM C1060", "ASTM C1153 (roof moisture)"],
    },
    "thermal_pipeline": {
        "name": "Thermal anomaly - pipeline (leak / insulation damage / blockage)",
        "description": "Local anomaly along pipe or ground above it (hot or cold depending on product temperature).",
        "causes": ["Leak at weld/flange/valve", "Damaged / wet insulation (CUI risk)", "Blockage / deposit build-up"],
        "consequences": ["Product loss, environmental & fire hazard", "Corrosion under insulation (CUI)"],
        "solution": ["Isolate & repair leak (clamp, sleeve, cut-out)", "Strip, inspect, recoat and reinsulate", "Pigging / cleaning for deposits"],
        "improvement": ["Leak detection system (SCADA/acoustic)", "Risk-based inspection programme (API 580/581)"],
        "actions": ["Gas detection / ground verification", "UT/RT at anomaly", "CUI inspection"], "ndt": ["UT", "RT", "Pulsed eddy current"],
        "references": ["API 570", "API 580/581", "ASME B31.4 / B31.8", "OISD standards (India)"],
    },
}
# Map civil/electrical thermal modes to KB keys and threshold keys
THERMAL_MODES = {
    "electrical": ("thermal_electrical", None), "pv": ("thermal_pv", "thermal_pv"),
    "civil_warm": ("thermal_civil_warm", "thermal_civil_warm"), "seepage_cold": ("thermal_seepage_cold", "thermal_seepage_cold"),
    "building": ("thermal_building", "thermal_building"), "pipeline": ("thermal_pipeline", "thermal_pipeline"),
}

# =============================================================================
# 3. ASSET PROFILES  (detectors -> defect keys, components, manual checks)
#    detectors: quality crack spall corrosion efflorescence vegetation moisture solar
# =============================================================================
ASSETS = {
    "bridge": {
        "label": "Bridge / Flyover / ROB / Culvert",
        "roi": "structure", "veg_rule": "on_structure", "buffer_m": 0.5, "grey_vehicles": True,
        "detectors": ["quality", "crack", "spall", "corrosion", "efflorescence", "vegetation"],
        "thermal_mode": "civil_warm",
        "map": {"crack": "crack_concrete", "spall": "spalling", "corrosion": "corrosion_concrete", "vegetation": "vegetation_structure"},
        "components": ["Deck slab & wearing coat", "Girders / box (soffit, webs)", "Diaphragms / cross girders", "Bearings & pedestals",
                       "Expansion joints", "Piers / pier caps", "Abutments / wing & return walls", "Foundations / scour zone",
                       "Parapets / crash barriers / railings", "Drainage spouts", "Approach slabs & embankment"],
        "manual_checklist": ["Bearings: tilt, displacement, bulging/cracking of elastomer, seized rollers, anchor bolts",
                             "Expansion joints: debris, seal rupture, misalignment, noise, water leakage",
                             "Scour depth around piers/abutments (sounding/echo-sounder; compare with max scour level)",
                             "Deck profile / sag (levelling survey); vibration under traffic",
                             "Prestress anchorage zones & cable ducts (grout voids)", "Load posting signage, crash barrier integrity",
                             "Waterway obstruction, debris accumulation, river training works"],
        "frequency": "Routine: pre- & post-monsoon visual; Principal/detailed: every 5 yr (IRC:SP:35); Special: after flood, earthquake, impact",
        "references": ["IRC:SP:35-1990", "IRC:SP:52-1999", "IRC:SP:40-2019", "IRC:112-2020", "IRC:SP:51 (load testing)", "FHWA BIRM / NBIS"],
    },
    "canal": {
        "label": "Canal (lined / unlined) & Structures",
        "roi": "canal", "veg_rule": "prism_all_berm_woody", "buffer_m": 8.0, "grey_vehicles": True,
        "detectors": ["quality", "crack", "spall", "vegetation", "moisture"],
        "thermal_mode": "seepage_cold",
        "map": {"crack": "crack_lining", "spall": "spalling", "vegetation": "vegetation_canal"},
        "components": ["Bed lining", "Side-slope lining", "Contraction / expansion joints", "Inner & outer embankment slopes",
                       "Service road / berm", "Cross-drainage works (aqueduct, siphon, super-passage)", "Regulators, falls, escapes, outlets",
                       "Toe drains / catch-water drains"],
        "manual_checklist": ["Freeboard and water level vs FSL; gauge readings", "Silt depth vs design bed level (cross-sections)",
                             "Breaches, rain-cuts, rodent holes, sloughing of slopes", "Gate operation & hoists of regulators",
                             "Unauthorised outlets / encroachments / pipe crossings", "Discharge measurement (current meter / ADCP) vs design"],
        "frequency": "Pre- & post-irrigation season; during closure for repairs; after heavy rainfall",
        "references": ["IS 3873", "IS 10430", "IS 5331", "IS 9451", "IS 4558 (under-drainage of lined canals)", "CWC / State irrigation manuals"],
    },
    "ht_line": {
        "label": "HT / EHV Transmission Line",
        "roi": "tower", "veg_rule": "zone_woody", "zone_independent": True, "min_coverage_pct": 0.3, "max_process_side_px": 2400,
        "detectors": ["quality", "corrosion", "vegetation"],
        "thermal_mode": "electrical",
        "map": {"corrosion": "corrosion_steel", "vegetation": "vegetation_row"},
        "components": ["Tower members (legs, bracings, cross-arms)", "Foundations / chimney & coping", "Insulator strings (disc / long-rod / polymer)",
                       "Hardware fittings (clamps, arcing horns, corona rings)", "Conductors & jumpers", "Mid-span / dead-end joints",
                       "Spacers / spacer-dampers / vibration dampers", "Earthwire / OPGW", "Danger plates, anti-climbing devices, number plates", "RoW corridor"],
        "manual_checklist": ["Missing / loose bolts and members (close visual from zoom imagery)", "Insulator flashover marks, broken / chipped discs, polymer tracking/erosion",
                             "Bird nests on cross-arms / insulators", "Conductor strand breaks, bird-caging", "Damper displacement / missing spacers",
                             "Ground clearance at max sag: CEA Regs - 5.2 m + 0.3 m per 33 kV (or part) above 33 kV (e.g. 220 kV 7.0 m, 400 kV 8.8 m)",
                             "Foundation: soil erosion, coping cracks, waterlogging, theft of members", "Earthing / tower footing resistance"],
        "frequency": "Ground patrolling monthly (pre-monsoon critical); thermography annually at high load; detailed tower inspection annually",
        "references": ["CEA (Measures relating to Safety and Electric Supply) Regulations, 2023", "CEA Technical Standards for Construction of Electrical Plants & Electric Lines Regs, 2022",
                       "IS 5613 (Pt 2 & 3)", "IS 802", "NETA MTS Table 100.18", "MoP RoW Guidelines 2015"],
    },
    "solar": {
        "label": "Solar PV Plant (ground / rooftop)",
        "roi": "pv", "veg_rule": "zone_all", "grey_vehicles": True, "max_process_side_px": 2400, "veg_zone_label": "between rows / under tables of the PV array",
        "detectors": ["quality", "solar", "vegetation"],   # MMS corrosion needs oblique close-ups -> checklist
        "thermal_mode": "pv",
        "map": {"vegetation": "vegetation_solar", "corrosion": "corrosion_steel"},
        "components": ["PV modules", "Module mounting structure / trackers", "String cables & MC4 connectors", "String combiner boxes / SCBs",
                       "Inverters", "Transformers / switchyard", "Drainage & roads", "Perimeter fence & vegetation"],
        "manual_checklist": ["Module mounting structure corrosion / galvanising loss (oblique close-up imagery or walk-down)",
                             "Irradiance (>= 600 W/m2), module temperature, wind, cloud cover at scan time (IEC TS 62446-3)",
                             "String-level IV curves on anomalous strings", "Torque of structure bolts, tracker alignment",
                             "Cable management (sagging, UV damage, rodent damage)", "Earthing continuity & lightning protection", "Inverter / SCB thermal scan (handheld)"],
        "frequency": "Aerial IR: annually (and at commissioning / warranty end); cleaning per soiling rate; vegetation monthly in monsoon",
        "references": ["IEC TS 62446-3:2017", "IEC 62446-1", "IEC 61724-1", "IEC 61215 / 61730", "MNRE / CEA guidelines"],
    },
    "road": {
        "label": "Road / Highway / Runway Pavement",
        "roi": "pavement", "veg_rule": "on_structure", "buffer_m": 1.0, "grey_vehicles": True,
        "detectors": ["quality", "crack", "spall", "vegetation"],   # RGB wet-patch detection unreliable under tree/vehicle shadows
        "thermal_mode": "civil_warm",
        "map": {"crack": "crack_pavement", "spall": "pothole", "vegetation": "vegetation_structure"},
        "components": ["Carriageway", "Paved / earthen shoulders", "Median & kerbs", "Side drains & culverts", "Road furniture & markings", "Embankment slopes"],
        "manual_checklist": ["Rutting depth (straight-edge)", "Roughness IRI", "Edge drop", "Markings retro-reflectivity", "Drain silting"],
        "frequency": "Routine monthly; condition survey bi-annual; structural evaluation every 3-5 yr",
        "references": ["IRC:82-2015", "IRC:81", "IRC:115", "IRC:SP:16 (roughness)", "ASTM D6433"],
    },
    "building": {
        "label": "Building / Facade / Industrial Structure",
        "roi": "facade", "veg_rule": "on_structure",
        "detectors": ["quality", "crack", "spall", "corrosion", "efflorescence", "vegetation"],
        "thermal_mode": "building",
        "map": {"crack": "crack_concrete", "spall": "spalling", "corrosion": "corrosion_concrete", "vegetation": "vegetation_structure"},
        "components": ["Roof & waterproofing", "Facade / cladding", "Columns & beams (exposed)", "Balconies / chajjas", "Parapets", "Rainwater pipes & drains", "Water tanks"],
        "manual_checklist": ["Internal dampness & seepage", "Structural audit per local bye-law (age-based)", "Fire safety & egress"],
        "frequency": "Annual visual; structural audit as per municipal bye-laws (typically buildings > 15-30 yr)",
        "references": ["IS 15988 (seismic evaluation & strengthening)", "IS 13935", "ISO 6781-1", "CPWD Handbook on Repair & Rehabilitation"],
    },
    "dam": {
        "label": "Dam / Barrage / Embankment / Levee",
        "roi": "structure", "earthen_fallback": True, "veg_rule": "zone_woody", "buffer_m": 2.0, "grey_vehicles": True,
        "zone_frame": True,       # woody growth / seepage anywhere on the visible embankment & toe area
        "veg_zone_label": "trees / shrubs on the embankment or near the toe (turf grass is not counted)",
        "detectors": ["quality", "crack", "spall", "efflorescence", "vegetation", "moisture"],
        "thermal_mode": "seepage_cold",
        "map": {"crack": "crack_concrete", "spall": "spalling", "vegetation": "vegetation_structure"},
        "components": ["Upstream face / riprap", "Downstream face & toe", "Crest & parapet", "Spillway & energy dissipator", "Galleries & drainage", "Abutment contacts", "Gates & hoists"],
        "manual_checklist": ["Seepage quantity & turbidity (V-notch)", "Piezometer & instrumentation readings", "Settlement/crest survey",
                             "Riprap displacement", "Spillway gate trial operation", "Sinkholes / boils downstream"],
        "frequency": "Pre- & post-monsoon (Dam Safety Act 2021); comprehensive safety evaluation every 10 yr",
        "references": ["Dam Safety Act 2021", "CWC Guidelines for Safety Inspection of Dams 2018", "IS 8237", "IS 7894 (slope stability)"],
    },
    "pipeline": {
        "label": "Pipeline (above-ground / corridor)",
        "roi": "linear", "veg_rule": "zone_all", "buffer_m": 3.0, "min_coverage_pct": 0.5, "max_process_side_px": 2400, "veg_zone_label": "in the pipeline right-of-way",   # a pipe is a thin band in frame
        "detectors": ["quality", "corrosion", "vegetation", "moisture"],
        "thermal_mode": "pipeline",
        "map": {"corrosion": "corrosion_steel", "vegetation": "vegetation_row"},
        "components": ["Pipe spool & welds", "Supports / sleepers", "Valves & flanges", "Insulation / cladding", "Crossings", "RoW & markers"],
        "manual_checklist": ["CP potentials (buried sections)", "Support settlement / pipe sag", "Third-party activity in RoW", "Leak / odour reports"],
        "frequency": "Patrolling per risk; RBI intervals (API 570)",
        "references": ["API 570", "API 580/581", "ASME B31.4/B31.8", "OISD-STD-138/141"],
    },
    "substation": {
        "label": "Substation / Switchyard",
        "roi": "yard", "veg_rule": "zone_all", "buffer_m": 0.0, "max_process_side_px": 2400, "veg_zone_label": "inside the switchyard",   # weeds INSIDE the yard only
        "detectors": ["quality", "corrosion", "vegetation"],
        "thermal_mode": "electrical",
        "map": {"corrosion": "corrosion_steel", "vegetation": "vegetation_structure"},
        "components": ["Power transformers & bushings", "Isolators / breakers", "CT / PT / LA", "Busbars & clamps", "Gantry structures", "Earth mat & gravel", "Cable trenches"],
        "manual_checklist": ["Oil leaks & levels", "DGA / oil tests", "Earth resistance", "Breaker timing & contact resistance"],
        "frequency": "Thermography quarterly-annual at load; routine monthly",
        "references": ["NETA MTS", "IS 10028", "CBIP Manual on Substations", "CEA Safety Regulations 2023"],
    },
}


# =============================================================================
# 4. CONFIG HELPERS
# =============================================================================
def deep_merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_config(path=CONFIG_PATH):
    try:
        if Path(path).exists():
            return deep_merge(DEFAULT_CONFIG, json.loads(Path(path).read_text(encoding="utf-8")))
    except Exception as e:
        print("Config load failed, using defaults:", e)
    return copy.deepcopy(DEFAULT_CONFIG)


def save_config(cfg, path=CONFIG_PATH):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def all_assets(cfg):
    a = copy.deepcopy(ASSETS)
    for k, v in (cfg.get("custom_assets") or {}).items():
        base = copy.deepcopy(a.get(v.get("extends", ""), {}))
        a[k] = deep_merge(base, v)
    return a


def kb_entry(cfg, key):
    e = copy.deepcopy(KB.get(key, {"name": key, "description": "", "causes": [], "consequences": [], "solution": [],
                                    "improvement": [], "actions": [], "ndt": [], "references": []}))
    e.update(copy.deepcopy((cfg.get("knowledge_base_overrides") or {}).get(key, {})))
    return e


def band(value, bands, default=None):
    """'>' semantics, highest matching band wins. bands=[[thr,sev],...]"""
    sev = default
    for thr, s in bands:
        if value > thr:
            sev = s
    return sev


def escalate(sev, n=1):
    return SEVERITIES[min(len(SEVERITIES) - 1, SEV_RANK[sev] + n)]


# =============================================================================
# 5. I/O & METADATA
# =============================================================================
def imread_any(path, flags=cv2.IMREAD_COLOR):
    data = np.fromfile(str(path), dtype=np.uint8)       # unicode-safe on Windows
    return cv2.imdecode(data, flags) if data.size else None


def imwrite_any(path, img, quality=88):
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if ok:
        buf.tofile(str(path))


def b64_jpg(img, width=420, quality=80):
    h, w = img.shape[:2]
    if w > width:
        img = cv2.resize(img, (width, int(h * width / w)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return base64.b64encode(buf.tobytes()).decode("ascii") if ok else ""


def _rat(x):
    try:
        return float(x)
    except Exception:
        try:
            return x[0] / x[1]
        except Exception:
            return None


def _dms(v, ref):
    try:
        d, m, s = (_rat(t) for t in v)
        val = d + m / 60.0 + s / 3600.0
        return -val if str(ref).upper().startswith(("S", "W")) else val
    except Exception:
        return None


def read_metadata(path):
    md = {"make": "", "model": "", "datetime": "", "lat": None, "lon": None, "abs_alt": None,
          "rel_alt": None, "gimbal_pitch": None, "gimbal_yaw": None, "focal_mm": None, "lrf_m": None}
    try:
        with Image.open(path) as im:
            ex = im.getexif()
            md["make"] = str(ex.get(271, "")).strip("\x00 ")
            md["model"] = str(ex.get(272, "")).strip("\x00 ")
            md["datetime"] = str(ex.get(306, "") or "")
            sub = ex.get_ifd(0x8769)
            if sub:
                md["focal_mm"] = _rat(sub.get(37386)) if sub.get(37386) else None
                md["datetime"] = str(sub.get(36867) or md["datetime"])
            gps = ex.get_ifd(0x8825)
            if gps:
                md["lat"] = _dms(gps.get(2), gps.get(1)) if gps.get(2) else None
                md["lon"] = _dms(gps.get(4), gps.get(3)) if gps.get(4) else None
                if gps.get(6) is not None:
                    alt = _rat(gps.get(6))
                    md["abs_alt"] = -alt if gps.get(5) in (1, b"\x01") and alt else alt
    except Exception:
        pass
    try:  # DJI XMP block (first 512 kB)
        with open(path, "rb") as f:
            head = f.read(512 * 1024)
        for key, dst in [("RelativeAltitude", "rel_alt"), ("AbsoluteAltitude", "abs_alt"),
                         ("GimbalPitchDegree", "gimbal_pitch"), ("GimbalYawDegree", "gimbal_yaw"),
                         ("LRFTargetDistance", "lrf_m")]:
            m = re.search(rb'drone-dji:' + key.encode() + rb'="\s*([+-]?[\d.]+)"', head) or \
                re.search(rb'<drone-dji:' + key.encode() + rb'>\s*([+-]?[\d.]+)<', head)
            if m:
                md[dst] = float(m.group(1))
    except Exception:
        pass
    return md


def parse_dji_srt(path):
    """Return list of (t_start_s, t_end_s, lat, lon, rel_alt) from DJI .SRT sidecar."""
    out = []
    try:
        txt = Path(path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return out
    tc = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")
    for blk in re.split(r"\n\s*\n", txt):
        m = tc.search(blk)
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        t0 = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000.0
        t1 = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000.0
        lat = re.search(r"latitude\s*:\s*([-\d.]+)", blk)
        lon = re.search(r"longt?itude\s*:\s*([-\d.]+)", blk)
        alt = re.search(r"rel_alt\s*:\s*([-\d.]+)", blk)
        if lat and lon:
            out.append((t0, t1, float(lat.group(1)), float(lon.group(1)), float(alt.group(1)) if alt else None))
            continue
        gm = re.search(r"GPS\s*\(\s*([-\d.]+)\s*,\s*([-\d.]+)\s*,\s*([-\d.]+)", blk)  # older: GPS(lon,lat,alt)
        if gm:
            out.append((t0, t1, float(gm.group(2)), float(gm.group(1)), float(gm.group(3))))
    return out


def pick_preset(md, cfg, img_w, thermal):
    g = cfg["geometry"]
    name = g["thermal_camera_preset" if thermal else "camera_preset"]
    presets = cfg["camera_presets"]
    if name != "auto":
        return presets.get(name)
    model = (md.get("model") or "").upper().replace(" ", "")
    for p in presets.values():
        if bool(p.get("thermal")) != bool(thermal):
            continue
        if any(t.upper().replace(" ", "") in model for t in p.get("match", []) if t) and \
                (not p.get("width_px") or abs(p["width_px"] - img_w) <= 2):
            return p
    return None


def compute_gsd_mm(md, img_w, cfg, thermal=False):
    """GSD (mm/px) at native resolution. Returns (gsd or None, basis string)."""
    g = cfg["geometry"]
    ov = g["thermal_gsd_override_mm_px" if thermal else "gsd_override_mm_px"]
    if ov and ov > 0:
        return float(ov), "user override"
    p = pick_preset(md, cfg, img_w, thermal)
    if not p:
        return None, "camera not matched (set preset or GSD override)"
    basis = "altitude"
    dist = g.get("object_distance_m") or 0
    if dist > 0:
        basis = "object distance (user)"
    elif md.get("lrf_m"):
        dist, basis = md["lrf_m"], "laser range finder"
    elif md.get("rel_alt"):
        dist = md["rel_alt"]
        pitch = md.get("gimbal_pitch")
        if g.get("use_gimbal_slant_range") and pitch is not None and 10 < abs(pitch) < 80:
            dist = dist / math.sin(math.radians(abs(pitch)))
            basis = "slant range (alt/sin pitch)"
    if not dist or dist <= 0:
        return None, "no altitude / distance"
    focal = p.get("focal_mm") or md.get("focal_mm")
    gsd = p["sensor_w_mm"] * dist * 1000.0 / (focal * img_w)
    return gsd, basis


# =============================================================================
# 6. IMAGE ANALYSIS PRIMITIVES
# =============================================================================
def skeletonize(mask):
    m = (mask > 0).astype(np.uint8) * 255
    if hasattr(cv2, "ximgproc"):
        return cv2.ximgproc.thinning(m) > 0
    skel = np.zeros_like(m)
    el = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
    while cv2.countNonZero(m):
        er = cv2.erode(m, el)
        skel |= cv2.subtract(m, cv2.dilate(er, el))
        m = er
    return skel > 0


def skeleton_length(sk):
    """Length in px: orthogonal links 1, diagonal links sqrt(2), ignoring diagonal links
    already bridged by an orthogonal staircase (avoids double counting)."""
    s = np.pad(sk.astype(bool), 1)
    hv = (s[:, 1:] & s[:, :-1]).sum() + (s[1:, :] & s[:-1, :]).sum()
    d1 = s[1:, 1:] & s[:-1, :-1] & ~(s[:-1, 1:] | s[1:, :-1])
    d2 = s[1:, :-1] & s[:-1, 1:] & ~(s[:-1, :-1] | s[1:, 1:])
    return float(hv + math.sqrt(2) * (d1.sum() + d2.sum())) or float(s.sum())


def skeleton_junctions(sk):
    """Branch points: crossing number (0->1 transitions around 8-neighbourhood) >= 3."""
    s = np.pad(sk.astype(np.uint8), 1)
    H, W = sk.shape
    nb = [s[0:H, 1:W + 1], s[0:H, 2:W + 2], s[1:H + 1, 2:W + 2], s[2:H + 2, 2:W + 2],
          s[2:H + 2, 1:W + 1], s[2:H + 2, 0:W], s[1:H + 1, 0:W], s[0:H, 0:W]]
    cn = sum(((nb[i] == 0) & (nb[(i + 1) % 8] == 1)).astype(np.uint8) for i in range(8))
    return int(((cn >= 3) & sk.astype(bool)).sum())


def mask_components(mask, min_px):
    n, lab, st, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    out = np.zeros(mask.shape, np.uint8)
    for i in range(1, n):
        if st[i, cv2.CC_STAT_AREA] >= min_px:
            out[lab == i] = 1
    return out


def bbox_of(mask):
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    return [int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)]


def detect_cracks(gray, gsd, c, roi=None, bright=False, straight_min_px=None):
    """Returns (list of crack dicts, mask). gsd in mm/px of the processed image or None.
    straight_min_px: long straight segments (construction/expansion joints, member edges, lane lines,
    formwork & shadow lines) are erased first so a crack crossing a joint is measured on its own."""
    # light Gaussian (not a 5x5 median, which erases every crack narrower than ~3 px, i.e. all hairline-to-2 mm
    # cracks at close-range GSD); texture is handled by the adaptive threshold and the shape filters
    g0 = cv2.GaussianBlur(gray, (0, 0), 0.8)
    g = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(g0)
    k = int(c["blackhat_kernel_px"]) | 1
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    resp = cv2.morphologyEx(g, cv2.MORPH_TOPHAT if bright else cv2.MORPH_BLACKHAT, ker)
    resp = cv2.GaussianBlur(resp, (3, 3), 0)
    otsu, _ = cv2.threshold(resp, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # robust floor: background response mean + k*sigma (cracks are a sparse minority of pixels)
    t = max(otsu, c["min_contrast"], float(resp.mean() + c.get("sigma_k", 3.0) * resp.std()))
    m = (resp > t).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    if roi is not None:
        m &= roi.astype(np.uint8)
    if straight_min_px:
        # joints / formwork lines / shadow & member edges are often only partly above the crack threshold:
        # find them on a weaker mask with gap tolerance, and erase their full band width
        lm = (resp > 0.6 * t).astype(np.uint8)
        if roi is not None:
            lm &= roi.astype(np.uint8)
        lines = cv2.HoughLinesP(lm * 255, 1, np.pi / 360, threshold=int(straight_min_px * 0.6),
                                minLineLength=int(straight_min_px), maxLineGap=max(4, int(0.03 * straight_min_px)))
        th_erase = max(7, int(c["max_mean_width_px"]) + 3)
        for x1, y1, x2, y2 in (lines.reshape(-1, 4) if lines is not None else []):
            cv2.line(m, (int(x1), int(y1)), (int(x2), int(y2)), 0, th_erase)
    n, lab, st, _ = cv2.connectedComponentsWithStats(m, 8)
    keep = np.zeros_like(m)
    cracks = []
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if max(w, h) < c["min_length_px"]:
            continue
        comp = (lab[y:y + h, x:x + w] == i).astype(np.uint8)
        sk = skeletonize(comp)
        L = skeleton_length(sk)
        if L < c["min_length_px"]:
            continue
        mean_w = a / L
        if mean_w > c["max_mean_width_px"] or L / max(mean_w, 1.0) < c["min_elongation"]:
            continue
        junc = skeleton_junctions(sk)
        if junc / L > c.get("max_junctions_per_px", 0.08):   # dense noise network, not a crack
            continue
        dist = cv2.distanceTransform(np.pad(comp, 1), cv2.DIST_L2, 3)[1:-1, 1:-1]
        wv = np.maximum(2.0 * dist[sk] - 1.0, 1.0)          # pixel-centre width convention
        ys, xs = np.nonzero(sk)
        ang, chord = 0.0, float(max(w, h))
        if len(xs) > 2:
            cov = np.cov(np.vstack([xs, ys]).astype(np.float64))
            ev, evec = np.linalg.eigh(cov)
            vx, vy = evec[:, 1]
            proj = (xs - xs.mean()) * vx + (ys - ys.mean()) * vy
            chord = float(proj.max() - proj.min()) or 1.0
            ang = abs(math.degrees(math.atan2(vy, vx)))
            ang = 180 - ang if ang > 90 else ang
        cracks.append({"bbox": [int(x), int(y), int(w), int(h)], "length_px": L, "chord_px": chord,
                       "mask": (x, y, comp),
                       "w_med_px": float(np.median(wv)), "w_max_px": float(np.percentile(wv, 95)),
                       "angle": ang, "junctions": junc})
    if cracks and not bright:
        crack_widths_photometric(g0, cracks, c, ker)
    for cr in cracks:
        x, y, comp = cr["mask"]
        keep[y:y + comp.shape[0], x:x + comp.shape[1]] |= comp
        if gsd:
            cr["length_m"] = cr["length_px"] * gsd / 1000.0
            cr["w_med_mm"] = cr["w_med_px"] * gsd
            cr["w_max_mm"] = cr["w_max_px"] * gsd
    return cracks, keep


def crack_widths_photometric(g0, cracks, c, ker):
    """Crack width from the integrated darkness deficit across the crack (blur- and threshold-independent):
    width = deficit per unit length / (local surface level x void darkness). The thresholded-mask width is
    >= ~3 px for any crack (blur + morphology) and over-states hairline cracks 2-6x. Void darkness = observed
    relative depth where the crack is resolved, else c["crack_void_darkness"] (sub-pixel cracks)."""
    g0 = g0.astype(np.float32)
    clo = cv2.morphologyEx(g0, cv2.MORPH_CLOSE, ker)
    bh = clo - g0
    H, W = g0.shape
    r = int(c["max_mean_width_px"] // 2 + 3)
    disk = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    kap0 = c.get("crack_void_darkness", 0.6)
    for cr in cracks:
        x, y, comp = cr["mask"]
        h, w = comp.shape
        p = r + 3
        x0, y0, x1, y1 = max(0, x - p), max(0, y - p), min(W, x + w + p), min(H, y + h + p)
        M = np.zeros((y1 - y0, x1 - x0), np.uint8)
        M[y - y0:y - y0 + h, x - x0:x - x0 + w] = comp
        Md = dilate(M, 2)
        ring = (dilate(M, r) > 0) & (Md == 0)
        b = bh[y0:y1, x0:x1]
        bias = float(np.median(b[ring])) if ring.any() else 0.0      # surface texture level
        dd = np.maximum(b - bias, 0) * Md
        S = cv2.filter2D(dd, -1, disk.astype(np.float32), borderType=cv2.BORDER_CONSTANT) / (2 * r + 1)
        cl = np.maximum(clo[y0:y1, x0:x1], 1.0)
        depth = cv2.dilate(dd / cl, disk)
        sk = skeletonize(M)
        if not sk.any():
            continue
        # where branches meet, the disk holds 2+ crack arms: measure away from junctions
        s8 = np.pad(sk.astype(np.uint8), 1)
        nb = sum(np.roll(np.roll(s8, dy, 0), dx, 1) for dy in (-1, 0, 1) for dx in (-1, 0, 1)
                 if dy or dx)[1:-1, 1:-1]
        far = sk & (dilate((sk & (nb >= 3)).astype(np.uint8), r) == 0)
        if far.sum() >= 0.3 * sk.sum():
            sk = far
        wmap = np.zeros(sk.shape, np.float32)
        wmap[sk] = S[sk] / (np.maximum(depth[sk], kap0) * cl[sk])
        # max width = widest SECTION, not the noisiest pixel: average along the crack over ~3 px first
        skf = sk.astype(np.float32)
        wsm = cv2.GaussianBlur(wmap, (0, 0), 3.0) / np.maximum(cv2.GaussianBlur(skf, (0, 0), 3.0), 1e-6)
        cr["w_med_px"] = float(np.median(wmap[sk]))
        cr["w_max_px"] = float(np.percentile(wsm[sk], 95))


def color_mask_hsv(bgr, lo, hi, open_k=3):
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((open_k, open_k), np.uint8))
    return (m > 0).astype(np.uint8)


def vegetation_mask(bgr, exg_min):
    f = bgr.astype(np.float32)
    b, g, r = f[..., 0], f[..., 1], f[..., 2]
    s = b + g + r + 1e-6
    exg = (2 * g - r - b) / s
    m = (exg > exg_min) & (g > r) & (g > b) & (g > 40)
    m = cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    return m


def efflorescence_mask(bgr, c):
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    m = ((hsv[..., 1] <= c["s_max"]) & (hsv[..., 2] >= c["v_min"])).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    # shapes are judged on the gap-closed object (noise/texture fragments a white panel into pieces)
    mc = fill_holes(cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8)))
    n, lab, st, _ = cv2.connectedComponentsWithStats(mc, 8)
    H, W = m.shape
    drop = np.zeros(n, bool)
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if (y == 0 and a > 0.02 * H * W) or a > 0.25 * H * W:      # sky / large uniform bright areas
            drop[i] = True
            continue
        # salt deposits / leaching streaks have irregular outlines; lane & kerb markings, painted bands,
        # AC units, signs, window frames and white vehicle panels are rectilinear
        if rectangularity(lab[y:y + h, x:x + w] == i)[0] > c.get("max_rectangularity", 0.85):
            drop[i] = True
    return (m & (1 - drop[lab].astype(np.uint8))).astype(np.uint8)


def spall_mask(gray, c, exclude=None, min_thick_px=None, gsd=None):
    """min_thick_px: a spall/pothole is an areal depression; components most of whose area is thinner
    than this (railing/baluster shadows, graffiti strokes, joints, crack networks) are rejected."""
    H, W = gray.shape
    k = max(61, (max(H, W) // 8) | 1)        # background window must be >> defect size
    # robust surface level: median (computed at 1/4 scale) is not pulled down/up by the dark defect itself
    # or by bright neighbours (footpath beside asphalt, white vehicle, bright patch)
    f = 4
    sm = cv2.resize(gray, (max(1, W // f), max(1, H // f)), interpolation=cv2.INTER_AREA)
    local = cv2.resize(cv2.medianBlur(sm, max(3, (k // f) | 1)), (W, H), interpolation=cv2.INTER_LINEAR).astype(np.float32)
    dark = ((local - gray.astype(np.float32)) > c["local_drop"]).astype(np.uint8)
    dark = cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    dark = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    if exclude is not None:
        dark[exclude > 0] = 0
    n, lab, st, _ = cv2.connectedComponentsWithStats(dark, 8)
    out = np.zeros_like(dark)
    tot = H * W
    for i in range(1, n):
        x, y, w, h, a = st[i]
        pct = 100.0 * a / tot
        small = (a * (gsd / 1000.0) ** 2 < c["min_area_m2"]) if gsd else (pct < c["min_area_pct"])
        if small or pct > c["max_area_pct"]:
            continue
        comp = (lab[y:y + h, x:x + w] == i).astype(np.uint8)
        # shape tests on the WHOLE dark object: a dark plate / recess / cast shadow larger than ~half the
        # background window is flagged only as window-shaped (concave) fragments that pass every shape test.
        # Grow the fragment to all connected pixels darker than midway between it and its surroundings.
        p = k // 2
        x0, y0, x1, y1 = max(0, x - p), max(0, y - p), min(W, x + w + p), min(H, y + h + p)
        cf = np.zeros((y1 - y0, x1 - x0), np.uint8)
        cf[y - y0:y - y0 + h, x - x0:x - x0 + w] = comp
        g = gray[y0:y1, x0:x1]
        ring = (dilate(cf, 9) > 0) & (cf == 0)
        if ring.any():
            t_mid = 0.5 * (float(np.median(g[cf > 0])) + float(np.percentile(g[ring], 75)))
            grow = cv2.morphologyEx((g < t_mid).astype(np.uint8), cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
            if exclude is not None:
                grow[exclude[y0:y1, x0:x1] > 0] = 0
            gn, gl = cv2.connectedComponents(grow | cf, connectivity=8)
            ids = np.unique(gl[cf > 0])
            obj = np.isin(gl, ids[ids > 0]).astype(np.uint8)
        else:
            obj = cf
        oa = int(obj.sum())
        if 100.0 * oa / tot > c["max_area_pct"]:          # part of a large dark area (shaded member, roadway)
            continue
        touch = ((obj[0].any() and y0 > 0) or (obj[-1].any() and y1 < H) or (obj[:, 0].any() and x0 > 0) or (obj[:, -1].any() and x1 < W))
        if touch:                                          # grown object exceeds the analysis window: large dark area
            continue
        # a spall / pothole is clearly darker than ITS OWN surroundings (same surface); mottling of a dark
        # surface next to a bright one only looks dark against the wide-window background
        rr_ = max(3, int(0.5 * math.sqrt(oa)))
        ring2 = (dilate(obj, rr_) > 0) & (obj == 0)
        if exclude is not None:
            ring2 &= exclude[y0:y1, x0:x1] == 0
        # 30th percentile: darker on ALL sides (a bright marking / kerb on one side must not carry the test);
        # tested on the grown object AND on the detected fragment itself (the grown object can be window-clipped)
        if ring2.sum() > 10 and float(np.percentile(g[ring2], 30)) - float(np.median(g[obj > 0])) < c["ring_drop"]:
            continue
        if ring2.sum() < 0.5 * ((dilate(obj, rr_) > 0) & (obj == 0)).sum():
            continue          # surroundings mostly vehicle / shadow / vegetation: contrast cannot be verified
        ys_, xs_ = np.nonzero(obj)
        ow, oh = int(xs_.max() - xs_.min() + 1), int(ys_.max() - ys_.min() + 1)
        obj = obj[ys_.min():ys_.max() + 1, xs_.min():xs_.max() + 1]
        if max(ow, oh) / max(1, min(ow, oh)) > 4:         # elongated -> crack/shadow edge, not spall
            continue
        cnts, _ = cv2.findContours(obj, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        hull = cv2.convexHull(max(cnts, key=cv2.contourArea))
        solidity = oa / max(cv2.contourArea(hull), 1)
        if rectangularity(obj)[0] > c.get("max_rectangularity", 0.85):   # man-made object / cast shadow
            continue
        if min_thick_px and min_thick_px > 2:
            kk = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (int(min_thick_px) | 1, int(min_thick_px) | 1))
            body = cv2.morphologyEx(obj, cv2.MORPH_OPEN, kk)
            if body.sum() < 0.5 * oa:
                continue
            # shape of the body once thin attachments (kerb / edge lines it touched) are stripped: drain spouts,
            # bolt plates, hatches and recesses are rectangular
            if rectangularity(body)[0] > c.get("max_rectangularity", 0.85):
                continue
        if solidity <= c["max_solidity"]:
            out[y:y + h, x:x + w] |= comp
    return out


def moisture_mask(bgr, c, exclude=None, gsd=None):
    """Wet patch / seepage candidate: a broad, irregular area clearly DARKER than its own surroundings (wet soil,
    damp concrete), not saturated-coloured. Cast shadows are removed by the caller (exclusion near vegetation,
    structures, vehicles); low confidence - corroborate with thermal (cold anomaly)."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    v, s_ = hsv[..., 2], hsv[..., 1]
    H, W = v.shape
    f = 4
    k = max(31, (max(H, W) // (8 * f)) | 1)
    sm = cv2.resize(v, (max(1, W // f), max(1, H // f)), interpolation=cv2.INTER_AREA)
    local = cv2.resize(cv2.medianBlur(sm, min(k, 255)), (W, H), interpolation=cv2.INTER_LINEAR).astype(np.int16)
    m = (((local - v.astype(np.int16)) > c["drop"]) & (s_ <= c["s_max"])).astype(np.uint8)
    if exclude is not None:
        m[exclude > 0] = 0
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    min_px = int(c["min_area_m2"] * (1000.0 / gsd) ** 2) if gsd else int(c["min_area_pct"] / 100.0 * H * W)

    def wet(st, i, lab):
        x, y, w, h, a = st
        if a < min_px:
            return False
        comp = lab[y:y + h, x:x + w] == i
        r_, rect_ = rectangularity(comp)
        if r_ > 0.85:                                  # slabs, roofs, tanks, shadows of boxes
            return False
        if rect_ is not None and max(rect_[1]) > 3.5 * max(1.0, min(rect_[1])):
            return False                               # roads, tracks, channels, drains: long bands, not seepage blobs
        pad = max(3, int(0.5 * math.sqrt(a)))
        x0, y0, x1, y1 = max(0, x - pad), max(0, y - pad), min(W, x + w + pad), min(H, y + h + pad)
        cc = (lab[y0:y1, x0:x1] == i).astype(np.uint8)
        ring = (dilate(cc, pad) > 0) & (cc == 0)
        vv = v[y0:y1, x0:x1]
        return ring.sum() > 10 and float(np.median(vv[ring])) - float(np.median(vv[cc > 0])) >= 0.7 * c["drop"]
    return keep_components(m, wet)


def pv_panel_mask(bgr):
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    m = ((v < 150) & (((h >= 90) & (h <= 135) & (s > 40)) | ((s < 60) & (v < 110)))).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
    return mask_components(m, int(0.005 * m.size))


def soiling_mask(bgr, panel, c, gsd=None, mod_px=None):
    """Soiling / bird droppings / deposits = blobs clearly BRIGHTER than the clean module surface of the
    same table. Dust, droppings and salt raise reflectance of dark blue/black cells whatever their hue.
    Reference = median lightness of the table's module surface (shadow strips below it, cell gaps and
    frames above it, are excluded by percentile trimming). Thin bright lines (cell gaps, frames,
    busbars) are removed by an opening sized to the smallest reportable deposit; specular glare
    (large, saturated) is rejected."""
    # cell-scale median removes busbar / cell-gap texture so the clean-module spread (MAD) is honest
    L = cv2.medianBlur(cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)[..., 0], 5).astype(np.float32)
    out = np.zeros(panel.shape, np.uint8)
    clean = np.zeros(panel.shape, np.uint8)      # clean module surface of each table
    n, lab, st, _ = cv2.connectedComponentsWithStats(panel.astype(np.uint8), 8)
    sig = (0.5 * 1000.0 / gsd) if gsd else 15.0          # ~half a module: follows sky-reflection / exposure gradients
    for i in range(1, n):
        x, y, w, h, a = st[i]
        sel = lab[y:y + h, x:x + w] == i
        Lt = L[y:y + h, x:x + w]
        vals = Lt[sel]
        if vals.size < 50:
            continue
        lo, hi = np.percentile(vals, [25, 75])
        # local clean-module reference: table pixels clipped to the inter-quartile range (deposits, shadows and
        # frames do not pull it), smoothed by normalised convolution at module scale
        wgt = sel.astype(np.float32)
        refm = cv2.GaussianBlur(np.clip(Lt, lo, hi) * wgt, (0, 0), sig) / np.maximum(cv2.GaussianBlur(wgt, (0, 0), sig), 1e-3)
        res = (Lt - refm)[sel]
        core = res[(res >= np.percentile(res, 25)) & (res <= np.percentile(res, 75))]
        mad = float(np.median(np.abs(core - np.median(core)))) * 1.4826 * 1.9   # IQR-trimmed MAD -> full-spread sigma
        out[y:y + h, x:x + w] |= ((Lt - refm > max(c["dl_min"], c["dl_sigma"] * mad)) & sel).astype(np.uint8)
        tol = max(c["dl_min"], 2.5 * mad)
        clean[y:y + h, x:x + w] |= ((np.abs(Lt - refm) <= tol) & sel).astype(np.uint8)
    # remove the module grid (frames, inter-module gaps, cell gaps): bright straight lines along the array axes
    _, rect = rectangularity(panel)
    if rect is not None and out.any():
        H, W = out.shape
        M = cv2.getRotationMatrix2D((W / 2, H / 2), ((rect[2] + 45.0) % 90.0) - 45.0, 1.0)
        cs, sn = abs(M[0, 0]), abs(M[0, 1])
        Wr, Hr = int(round(H * sn + W * cs)), int(round(H * cs + W * sn))
        M[0, 2] += (Wr - W) / 2
        M[1, 2] += (Hr - H) / 2
        r = cv2.warpAffine(out, M, (Wr, Hr), flags=cv2.INTER_NEAREST)
        Lk = max(9, int(0.6 * 1000.0 / gsd)) if gsd else max(9, min(H, W) // 60)   # >= 0.6 m runs = grid lines
        lines = cv2.morphologyEx(r, cv2.MORPH_OPEN, np.ones((1, Lk), np.uint8)) | cv2.morphologyEx(r, cv2.MORPH_OPEN, np.ones((Lk, 1), np.uint8))
        bw = (max(5, int(round(0.15 * 1000.0 / gsd)) + 2) if gsd else 7) | 1      # thicker than any frame/gap band
        blobs = cv2.morphologyEx(r, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (bw, bw)))
        lines = cv2.dilate(lines & (1 - cv2.dilate(blobs, np.ones((3, 3), np.uint8))), np.ones((3, 3), np.uint8))   # thin lines only
        # grid neighbourhood = thin line arms grown by the blob size, so the crossings between arms are covered
        grid_all = cv2.warpAffine(cv2.dilate(lines, np.ones((bw, bw), np.uint8)), cv2.invertAffineTransform(M), (W, H), flags=cv2.INTER_NEAREST)
        back = cv2.warpAffine(lines, cv2.invertAffineTransform(M), (W, H), flags=cv2.INTER_NEAREST)
        out = out & (1 - back)
    else:
        grid_all = np.zeros_like(out)
    # mapping GSD: a deposit must be wider than a frame + gap band (~0.1 m), else grid-line fragments pass.
    # close range (<= close_gsd_mm): frames are resolved as long lines and removed, so droppings (~4 cm) count
    close = bool(gsd) and gsd <= c["close_gsd_mm"]
    if gsd:
        dk = (max(5, int(round((c["min_blob_close_m"] if close else c["min_blob_m"]) * 1000.0 / gsd)) + (0 if close else 2))) | 1
    else:
        dk = 5
    out = cv2.morphologyEx(out, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dk, dk)))
    # frame/gap crossings survive line removal as small crosses: a deposit lies mostly on the glass, not on the grid
    out = keep_components(out, lambda st_, j, lb: float(np.mean(grid_all[st_[1]:st_[1] + st_[3], st_[0]:st_[0] + st_[2]]
                                                              [lb[st_[1]:st_[1] + st_[3], st_[0]:st_[0] + st_[2]] == j])) < 0.5)
    # a deposit sits ON the glass: its surroundings are mostly clean module surface. Bright ground or
    # gravel at a table edge is ringed by dark table shadow / land outside the array and is rejected.
    min_px = (max(40, int((c["min_area_close_m2"] if close else c["min_area_m2"]) * (1000.0 / gsd) ** 2))) if gsd else 40
    rr = max(3, dk)

    def on_glass(st_, j, lb):
        x, y, w, h, a = st_
        if a < min_px:
            return False
        r_, rect_ = rectangularity(lb[y:y + h, x:x + w] == j)
        if close and rect_ is not None and max(rect_[1]) > c["max_aspect"] * max(1.0, min(rect_[1])):
            return False                 # streak along a frame / edge reflection, not a compact deposit
        Hh, Ww = lb.shape
        x0, y0, x1, y1 = max(0, x - rr), max(0, y - rr), min(Ww, x + w + rr), min(Hh, y + h + rr)
        comp = (lb[y0:y1, x0:x1] == j).astype(np.uint8)
        ring = dilate(comp, rr) & (1 - comp)
        tot = ring.sum() + 2 * rr * ((x - rr < 0) * h + (y - rr < 0) * w + (x + w + rr > Ww) * h + (y + h + rr > Hh) * w)
        return tot > 0 and float((ring & clean[y0:y1, x0:x1]).sum()) / tot >= c["min_clean_ring_frac"]
    out = keep_components(out, on_glass)
    if mod_px:   # glare: module-sized, mostly saturated
        out = keep_components(out, lambda st_, j, lb: not (st_[4] > 0.5 * mod_px and
                              float(np.mean(L[st_[1]:st_[1] + st_[3], st_[0]:st_[0] + st_[2]][lb[st_[1]:st_[1] + st_[3], st_[0]:st_[0] + st_[2]] == j] >= 250)) > 0.5))
    return out


def off_axis_fraction(comp, sk):
    """Share of skeleton pixels whose local direction (structure tensor) is > 15 deg off both array axes."""
    f = cv2.GaussianBlur(comp.astype(np.float32), (0, 0), 1.0)
    gx, gy = cv2.Sobel(f, cv2.CV_32F, 1, 0), cv2.Sobel(f, cv2.CV_32F, 0, 1)
    jxx, jyy, jxy = (cv2.GaussianBlur(v, (0, 0), 2.0) for v in (gx * gx, gy * gy, gx * gy))
    t = np.degrees(0.5 * np.arctan2(2 * jxy, jxx - jyy))[sk > 0] % 90.0
    return float((np.minimum(t, 90.0 - t) > 15.0).mean()) if t.size else 0.0


def pv_glass_breakage(gray, panels, c, min_off_axis=0.35):
    """Fracture networks inside VERIFIED modules only. The regular cell/frame grid is removed first
    (morphological opening with long line kernels along the array axes) so only non-grid, branched
    bright lines remain. Threshold adapts to the image noise (like detect_cracks); remnants of the
    grid (mostly axis-aligned) are rejected; fragments of one fracture star are merged into one
    pattern. Returns list of (bbox, junctions)."""
    H, W = gray.shape
    _, rect = rectangularity(panels)            # array axis from the largest verified block
    ang = ((rect[2] if rect else 0.0) + 45.0) % 90.0 - 45.0    # minAreaRect angle is only defined modulo 90 deg
    M = cv2.getRotationMatrix2D((W / 2, H / 2), ang, 1.0)
    cs, sn = abs(M[0, 0]), abs(M[0, 1])
    Wr, Hr = int(round(H * sn + W * cs)), int(round(H * cs + W * sn))   # canvas holds the whole rotated frame
    M[0, 2] += (Wr - W) / 2
    M[1, 2] += (Hr - H) / 2
    Mi = cv2.invertAffineTransform(M)
    g = cv2.warpAffine(cv2.createCLAHE(2.0, (8, 8)).apply(gray), M, (Wr, Hr))
    pm = cv2.warpAffine(erode(panels, 3), M, (Wr, Hr), flags=cv2.INTER_NEAREST)
    resp = cv2.morphologyEx(g, cv2.MORPH_TOPHAT, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    L = max(15, min(H, W) // 40)
    grid = np.maximum(cv2.morphologyEx(resp, cv2.MORPH_OPEN, np.ones((1, L), np.uint8)),
                      cv2.morphologyEx(resp, cv2.MORPH_OPEN, np.ones((L, 1), np.uint8)))
    grid = cv2.dilate(grid, np.ones((3, 3), np.uint8))
    res = cv2.subtract(resp, grid)
    v = res[pm > 0].astype(np.float32)
    if v.size == 0:
        return []
    t = max(c["min_contrast"] * 2, 25, float(v.mean() + c.get("sigma_k", 3.0) * v.std()))   # sparse defect vs noise
    m = ((res > t) & (pm > 0)).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    keep = np.zeros_like(m)
    n, lab, st, _ = cv2.connectedComponentsWithStats(m, 8)
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if max(w, h) < c["min_length_px"] * 0.6:
            continue
        comp = lab[y:y + h, x:x + w] == i
        sk = skeletonize(comp)
        if skeleton_junctions(sk) >= 2 and skeleton_length(sk) >= c["min_length_px"] and off_axis_fraction(comp, sk) >= min_off_axis:
            keep[y:y + h, x:x + w] |= comp.astype(np.uint8)
    out = []
    r = max(3, int(c["min_length_px"] * 0.25))          # fragments of one fracture star -> one pattern
    n, lab, st, _ = cv2.connectedComponentsWithStats(cv2.dilate(keep, np.ones((2 * r + 1, 2 * r + 1), np.uint8)), 8)
    for i in range(1, n):
        x, y, w, h, a = st[i]
        j = skeleton_junctions(skeletonize(keep[y:y + h, x:x + w] & (lab[y:y + h, x:x + w] == i)))
        x, y, w, h = x + r, y + r, w - 2 * r, h - 2 * r
        pts = np.array([[x, y], [x + w, y], [x, y + h], [x + w, y + h]], np.float32)
        bp = cv2.transform(pts[None], Mi)[0]
        x0, y0 = np.clip(bp.min(0), 0, [W - 1, H - 1]).astype(int)
        x1, y1 = np.clip(bp.max(0), 0, [W - 1, H - 1]).astype(int)
        out.append(([int(x0), int(y0), int(x1 - x0 + 1), int(y1 - y0 + 1)], j))
    return out


# =============================================================================
# 6b. SCENE UNDERSTANDING & ASSET REGION-OF-INTEREST (ROI)
#     Every detector runs ONLY inside the asset ROI (or its defined buffer
#     zone), so objects near the asset - empty plots, fields, vehicles, roofs,
#     soil, water bodies - are never reported as asset defects.
#     Priority: manual mask file  >  automatic asset segmentation  >  (strict
#     mode) frame skipped & flagged  /  (non-strict) full frame, Low confidence.
# =============================================================================
def keep_components(mask, pred):
    """Vectorised component filter: pred(stats_row, index) -> bool."""
    n, lab, st, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    keep = np.zeros(n, bool)
    for i in range(1, n):
        keep[i] = bool(pred(st[i], i, lab))
    return keep[lab].astype(np.uint8)


def fill_holes(mask, max_hole_px=None):
    """Fill enclosed holes (optionally only those <= max_hole_px)."""
    m = mask.astype(np.uint8)
    inv = (m == 0).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(inv, 4)
    H, W = m.shape
    fill = np.zeros(n, bool)
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if x == 0 or y == 0 or x + w >= W or y + h >= H:
            continue
        fill[i] = max_hole_px is None or a <= max_hole_px
    return (m | fill[lab]).astype(np.uint8)


def local_std(gray, k):
    g = gray.astype(np.float32)
    m = cv2.blur(g, (k, k))
    return np.sqrt(np.maximum(cv2.blur(g * g, (k, k)) - m * m, 0))


def rectangularity(comp):
    cnts, _ = cv2.findContours(comp.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return 0.0, None
    c = max(cnts, key=cv2.contourArea)
    rect = cv2.minAreaRect(c)
    ra = rect[1][0] * rect[1][1]
    return (cv2.contourArea(c) / ra if ra > 0 else 0.0), rect


def px_per_m(gsd_mm):
    return 1000.0 / gsd_mm if gsd_mm else None


def buffer_px(gsd_mm, metres, pct, shape):
    """Physical buffer if GSD known, else % of the frame's smaller side."""
    if gsd_mm:
        return max(3, int(metres * 1000.0 / gsd_mm))
    return max(3, int(pct / 100.0 * min(shape)))


def dilate(mask, r):
    """Euclidean dilation by r px; distance transform for large r (cost independent of r)."""
    r = int(r)
    m = (mask > 0).astype(np.uint8)
    if r <= 0:
        return m
    if r <= 12:
        return cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)))
    if not m.any():
        return m
    return (cv2.distanceTransform(1 - m, cv2.DIST_L2, 5) <= r).astype(np.uint8)


def erode(mask, r):
    r = int(r)
    m = (mask > 0).astype(np.uint8)
    if r <= 0:
        return m
    if r <= 12:
        return cv2.erode(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)))
    # frame border is not an asset boundary (same convention as cv2.erode): pad with 1
    return (cv2.distanceTransform(np.pad(m, r + 1, constant_values=1), cv2.DIST_L2, 5)[r + 1:-r - 1, r + 1:-r - 1] > r).astype(np.uint8)


def scene_masks(bgr, gsd, rc):
    """Generic scene classes used for exclusion: sky, vegetation, woody vegetation, water, vehicles, grey."""
    H, W = bgr.shape[:2]
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    tex = local_std(gray, 15)
    veg = vegetation_mask(bgr, rc["exg_min"])
    # woody vegetation (trees/shrubs): textured, darker canopy; smooth crops/grass/turf excluded
    woody = (veg > 0) & (tex > rc["woody_texture_std"]) & (v < rc["woody_v_max"])
    woody = cv2.morphologyEx(woody.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    woody = cv2.morphologyEx(woody, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))
    woody = fill_holes(cv2.morphologyEx(woody, cv2.MORPH_CLOSE, np.ones((21, 21), np.uint8)), int(0.02 * H * W)) & veg
    min_tree = int((rc["woody_min_area_m2"] * px_per_m(gsd) ** 2) if gsd else 0.002 * H * W)
    woody = keep_components(woody, lambda st, i, lab: st[4] >= min_tree)
    # sky: bright, blue or neutral, connected to top border
    skyc = ((v > 150) & (((h >= 90) & (h <= 130)) | (s < 30))).astype(np.uint8)
    # sky is smooth: bright concrete/steel touching the top edge (close-range members, nadir decks) is textured
    sky = keep_components(skyc, lambda st, i, lab: st[1] == 0 and st[4] > 0.02 * H * W and
                          float(np.median(tex[st[1]:st[1] + st[3], st[0]:st[0] + st[2]][lab[st[1]:st[1] + st[3], st[0]:st[0] + st[2]] == i]))
                          < rc.get("sky_texture_std", 2.5))
    # water: smooth, blue/cyan/green-dark, large
    smooth = local_std(gray, 9) < rc["water_texture_std"]
    wat = smooth & (v < 180) & ((((h >= 75) & (h <= 130)) & (s > 30)) | ((h >= 35) & (h < 75) & (s > 50) & (v < 120)))
    water = keep_components(wat.astype(np.uint8), lambda st, i, lab: st[4] > rc["water_min_pct"] / 100.0 * H * W)
    water[veg > 0] = 0
    # vehicles: compact rectangles of vehicle size with strong colour / very bright / very dark tone
    veh = np.zeros((H, W), np.uint8)
    if rc.get("vehicle_exclusion", True):
        cand = ((s > 90) | (v > 215) | (v < 45)).astype(np.uint8)
        cand[veg > 0] = 0
        cand = cv2.morphologyEx(cand, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
        lo, hi = ((rc["vehicle_area_m2"][0] * px_per_m(gsd) ** 2, rc["vehicle_area_m2"][1] * px_per_m(gsd) ** 2) if gsd
                  else (0.0002 * H * W, 0.03 * H * W))

        def is_vehicle(st, i, lab):
            x, y, w, hh, a = st
            if not (lo <= a <= hi):
                return False
            comp = (lab[y:y + hh, x:x + w] == i)
            r, rect = rectangularity(comp)
            if rect is None:
                return False
            L, S = max(rect[1]), max(1.0, min(rect[1]))
            return r > 0.72 and 1.3 <= L / S <= 3.6
        veh = keep_components(cand, is_vehicle)
        f = 4
        sm = cv2.resize(gray, (max(1, W // f), max(1, H // f)), interpolation=cv2.INTER_AREA)
        kv = (int(min(251, max(15, (12.0 * px_per_m(gsd) / f) if gsd else min(H, W) / (8 * f)))) | 1)
        loc = cv2.resize(cv2.medianBlur(sm, kv), (W, H), interpolation=cv2.INTER_LINEAR).astype(np.int16)
        g16 = gray.astype(np.int16)
        # any-colour vehicles (silver/grey/beige) stand out from the local road surface: contrast-based candidates.
        # Only where vehicles park/drive ON the asset (roads, decks, service roads, crests, PV O&M tracks):
        # elsewhere grey rectangles of car size are asset components (transformers, kiosks, pipe supports)
        for con in (((g16 - loc) > 35, (loc - g16) > 35) if rc.get("grey_vehicles", True) else ()):
            con = con.astype(np.uint8)
            con[veg > 0] = 0
            con = cv2.morphologyEx(con, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
            veh |= keep_components(fill_holes(con, int(hi)), is_vehicle)
        # cast shadow: darker-than-road pixels within ~1.2 m of a vehicle (bounded by distance, not shape)
        if veh.any():
            reach = max(4, int(1.2 * px_per_m(gsd)) if gsd else int(0.01 * min(H, W)))
            veh |= (((loc - g16) > 25) & (dilate(veh, reach) > 0)).astype(np.uint8)
        veh = dilate(veh, 4)
    grey = ((s < rc["grey_s_max"]) & (v >= rc["grey_v_min"]) & (v <= rc["grey_v_max"])).astype(np.uint8)
    grey[(veg > 0) | (sky > 0) | (water > 0)] = 0
    return {"veg": veg, "woody": woody.astype(np.uint8), "sky": sky, "water": water, "vehicle": veh,
            "grey": grey, "gray": gray, "hsv": hsv, "tex": tex}


def grid_periodicity(gray, comp, rect, per_axis=False):
    """PV-module verification: strength of periodic cell/frame lines inside the component
    (normalised autocorrelation peak of the edge profile along both module axes).
    PV arrays score high; car roofs, water and tarpaulins score low. per_axis=True returns
    [(score, lag_px), (score, lag_px)] for the two component axes (the repeat length matters:
    corrugated-roof ribs and rows of parked cars are periodic too)."""
    (cx, cy), (rw, rh), ang = rect
    res = [(0.0, 0), (0.0, 0)]
    if rw < 12 or rh < 12:
        return res if per_axis else 0.0
    M = cv2.getRotationMatrix2D((cx, cy), ang, 1.0)
    H, W = gray.shape
    g = cv2.warpAffine(gray, M, (W, H), flags=cv2.INTER_LINEAR)
    m = cv2.warpAffine(comp.astype(np.uint8), M, (W, H), flags=cv2.INTER_NEAREST)
    x0, x1 = int(cx - rw / 2 + 0.1 * rw), int(cx + rw / 2 - 0.1 * rw)
    y0, y1 = int(cy - rh / 2 + 0.1 * rh), int(cy + rh / 2 - 0.1 * rh)
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
    if x1 - x0 < 10 or y1 - y0 < 10:
        return res if per_axis else 0.0
    sub = g[y0:y1, x0:x1].astype(np.float32)
    msub = m[y0:y1, x0:x1].astype(np.float32)
    for axis, sob in ((0, cv2.Sobel(sub, cv2.CV_32F, 1, 0)), (1, cv2.Sobel(sub, cv2.CV_32F, 0, 1))):
        e = np.abs(sob) * msub
        prof = e.sum(axis) / np.maximum(msub.sum(axis), 1)
        prof = prof - prof.mean()
        n = len(prof)
        if n < 12 or prof.std() < 1e-3:
            continue
        ac = np.correlate(prof, prof, "full")[n - 1:] / (prof.var() * n)
        lags = np.arange(len(ac))
        ok = (lags >= 3) & (lags <= n // 3)
        if ok.sum() < 3:
            continue
        a, lg = ac[ok], lags[ok]
        peaks = [(float(a[k]), int(lg[k])) for k in range(1, len(a) - 1) if a[k] >= a[k - 1] and a[k] >= a[k + 1]]
        if peaks:
            res[axis] = max(peaks)
    return res if per_axis else max(0.0, res[0][0], res[1][0])


def module_lattice(ax, gsd_mm, rc):
    """ax = grid_periodicity(..., per_axis=True). True if the dominant repeat is a MODULE pitch: portrait pitch
    (module width + gap) is unique to PV; a landscape-length pitch is shared with parked cars / parking bays,
    so it also needs module rows across. Roof ribs / cell-scale texture repeat far below module pitch."""
    k = int(ax[1][0] > ax[0][0])
    if ax[k][0] < rc["pv_min_grid_score"]:
        return False
    lo, mid, hi = rc["pv_pitch_m"]
    per, (sc2, lag2) = ax[k][1] * gsd_mm / 1000.0, ax[1 - k]
    return lo <= per <= mid or (mid < per <= hi and sc2 >= rc["pv_min_grid_score"] and 0.75 * lo <= lag2 * gsd_mm / 1000.0 <= mid)


def pv_array_roi(bgr, gsd, rc, pv, scene):
    """Verified PV modules (shape + size + periodic cell/frame grid) and the array zone."""
    H, W = bgr.shape[:2]
    cand = pv_panel_mask(bgr)
    cand[(scene["veg"] > 0) | (scene["water"] > 0)] = 0
    mod_px = (pv["module_length_m"] * pv["module_width_m"] * px_per_m(gsd) ** 2) if gsd else None
    min_a = max(rc["pv_min_modules"] * mod_px, 50) if mod_px else rc["pv_min_area_pct"] / 100.0 * H * W
    gray = scene["gray"]
    scores = []

    def check(comp):
        r, rect = rectangularity(comp)
        if r < rc["pv_min_rectangularity"]:
            return False
        ax = grid_periodicity(gray, comp, rect, per_axis=True)
        sc = max(0.0, ax[0][0], ax[1][0])
        scores.append(sc)
        if sc < rc["pv_min_grid_score"] or not gsd:
            return sc >= rc["pv_min_grid_score"]
        return module_lattice(ax, gsd, rc)          # GSD known: repeat length must be a module pitch

    panels = np.zeros((H, W), np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(cand.astype(np.uint8), 8)
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if a < min_a:
            continue
        comp = (lab == i).astype(np.uint8)
        if check(comp):
            panels |= comp
            continue
        if a < 3 * min_a:
            continue
        # dark modules merged with similar-looking ground (asphalt, wet soil, shade): split the region once
        # into its darker and lighter brightness classes and verify each part on its own
        v = gray[comp > 0]
        t, _ = cv2.threshold(v.reshape(-1, 1), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        for part in ((gray <= t) & (comp > 0), (gray > t) & (comp > 0)):
            part = cv2.morphologyEx(part.astype(np.uint8), cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
            pn, pl, ps, _ = cv2.connectedComponentsWithStats(part, 8)
            for j in range(1, pn):
                if ps[j, 4] >= min_a:
                    sub = (pl == j).astype(np.uint8)
                    if check(sub):
                        panels |= sub
    panels = fill_holes(panels, int(0.05 * H * W))
    scene["vehicle"][panels > 0] = 0            # a verified module grid is never a vehicle
    zr = buffer_px(gsd, rc["pv_zone_close_m"], rc["pv_zone_close_pct"], (H, W))
    zone = fill_holes(erode(dilate(panels, zr), zr))      # closing bridges inter-row gaps
    # array zone = modules + inter-row / under-table ground; a thin perimeter strip (shading risk)
    if rc["pv_perimeter_m"] > 0:
        zone = dilate(zone, buffer_px(gsd, rc["pv_perimeter_m"], 1.0, (H, W)))
    return panels, zone, {"pv_grid_scores": [round(s, 2) for s in scores]}


def structure_roi(scene, rc, min_pct, shape):
    H, W = shape
    g = cv2.morphologyEx(scene["grey"], cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    g = cv2.morphologyEx(g, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
    g[scene["vehicle"] > 0] = 0
    g = keep_components(g, lambda st, i, lab: st[4] >= min_pct / 100.0 * H * W)
    return fill_holes(g, int(0.25 * H * W))      # cracks/spalls/stains inside the member stay in ROI


def elongated(st, i, lab, H, W, min_aspect=3.0, min_span=0.5):
    x, y, w, h, a = st
    comp = lab[y:y + h, x:x + w] == i
    r, rect = rectangularity(comp)
    if rect is None:
        return False
    L, S = max(rect[1]), max(1.0, min(rect[1]))
    return L / S >= min_aspect or L >= min_span * max(H, W)


def pipe_band_roi(gray, gsd, rc):
    """Pipe = two long, straight, parallel edges one pipe diameter apart (rc["pipe_diameter_m"]) with a
    uniform interior. Returns the union of such bands."""
    H, W = gray.shape
    f = max(1, int(max(H, W) // 2000))                      # work at <= ~2000 px
    g = cv2.resize(gray, (W // f, H // f), interpolation=cv2.INTER_AREA) if f > 1 else gray
    h, w = g.shape
    gp = (gsd * f) if gsd else None
    dmin, dmax = ((rc["pipe_diameter_m"][0] * 1000 / gp, rc["pipe_diameter_m"][1] * 1000 / gp) if gp else (4, 0.05 * min(h, w)))
    e = cv2.Canny(cv2.GaussianBlur(g, (0, 0), 1.2), 30, 90)
    L = int(rc["pipe_min_len_frac"] * max(h, w))
    lines = cv2.HoughLinesP(e, 1, np.pi / 360, threshold=int(L * 0.5), minLineLength=L, maxLineGap=int(0.02 * max(h, w)))
    out = np.zeros((h, w), np.uint8)
    if lines is None:
        return cv2.resize(out, (W, H), interpolation=cv2.INTER_NEAREST)
    segs = []
    for x1, y1, x2, y2 in lines.reshape(-1, 4):
        a = math.atan2(y2 - y1, x2 - x1) % math.pi
        nx, ny = -math.sin(a), math.cos(a)
        segs.append((a, x1 * nx + y1 * ny, (x1, y1, x2, y2)))
    for i in range(len(segs)):
        for j in range(i + 1, len(segs)):
            a1, r1, s1 = segs[i]
            a2, r2, s2 = segs[j]
            if abs(((a1 - a2) + math.pi / 2) % math.pi - math.pi / 2) > math.radians(2):
                continue
            dr = abs(r1 - r2)
            if not (dmin <= dr <= dmax):
                continue
            poly = np.array([[s1[0], s1[1]], [s1[2], s1[3]], [s2[2], s2[3]], [s2[0], s2[1]]], np.int32)
            hull = cv2.convexHull(poly)
            band = np.zeros((h, w), np.uint8)
            cv2.fillPoly(band, [hull], 1)
            if band.sum() < 0.5 * dr * L:
                continue
            inner = g[erode(band, 1) > 0]
            ring = g[(dilate(band, max(3, int(dr))) > 0) & (band == 0)]
            # a pipe is a uniform body distinct from the ground on both sides
            if inner.size and ring.size and inner.std() < rc["pipe_max_interior_std"] and abs(float(np.median(inner)) - float(np.median(ring))) > 12:
                out |= band
    return cv2.resize(out, (W, H), interpolation=cv2.INTER_NEAREST)


def linear_structure_roi(bgr, scene, rc, gsd=None):
    """Pipelines etc.: parallel-edge bands of pipe diameter; fallback = elongated uniform-colour segments
    (k-means in Lab on a downsampled frame)."""
    H, W = bgr.shape[:2]
    band = pipe_band_roi(scene["gray"], gsd, rc)
    if band.any():
        band[(scene["veg"] > 0) | (scene["water"] > 0)] = 0
        return fill_holes(band, int(0.02 * H * W))
    s = 400.0 / max(H, W)
    small = cv2.resize(bgr, (max(8, int(W * s)), max(8, int(H * s))), interpolation=cv2.INTER_AREA)
    lab = cv2.cvtColor(small, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float32)
    _, labels, _ = cv2.kmeans(lab, 6, None, (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0), 3, cv2.KMEANS_PP_CENTERS)
    labels = labels.reshape(small.shape[:2])
    out = np.zeros(small.shape[:2], np.uint8)
    h, w = small.shape[:2]
    for c in range(6):
        m = cv2.morphologyEx((labels == c).astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        out |= keep_components(m, lambda st, i, lb: st[4] > 0.003 * h * w and elongated(st, i, lb, h, w, 6.0, 0.6))
    out = cv2.resize(out, (W, H), interpolation=cv2.INTER_NEAREST)
    out[(scene["veg"] > 0) | (scene["sky"] > 0) | (scene["water"] > 0)] = 0
    return fill_holes(out, int(0.02 * H * W))


def lattice_roi(scene, rc, shape):
    """Lattice towers / gantries: dense thin-member edge network on non-vegetated, low-saturation pixels."""
    H, W = shape
    edges = cv2.Canny(scene["gray"], 50, 150)
    edges[scene["veg"] > 0] = 0
    lowsat = (scene["hsv"][..., 1] < rc["grey_s_max"]).astype(np.float32)
    k = max(15, (min(H, W) // 40) | 1)
    dens = cv2.blur((edges > 0).astype(np.float32), (k, k))
    lows = cv2.blur(lowsat * (edges > 0), (k, k)) / np.maximum(dens, 1e-6)
    m = ((dens > rc["lattice_edge_density"]) & (lows > 0.5)).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    m = keep_components(m, lambda st, i, lab: st[4] >= 0.002 * H * W)
    return dilate(fill_holes(m, int(0.1 * H * W)), k // 2)


def find_manual_mask(path, in_dir, rc):
    if not path:
        return None
    p = Path(path)
    cands = [p.with_name(p.stem + suf) for suf in rc["manual_mask_suffixes"]]
    cands.append(Path(in_dir) / rc["mask_folder"] / (p.stem + ".png"))
    for c in cands:
        if c.exists():
            return c
    return None


def asset_roi(asset, bgr, gsd, cfg, scene, mask_path=None):
    """Returns dict(roi, core, zone, method, cover_pct, extra). roi = surface analysed for
    surface defects; core = roi eroded (edge artefacts removed) for cracks/spalls;
    zone = roi + asset-specific buffer for vegetation/moisture rules."""
    rc, H, W = cfg["roi"], bgr.shape[0], bgr.shape[1]
    kind = asset.get("roi", "full")
    extra = {}
    if mask_path:
        mk = imread_any(mask_path, cv2.IMREAD_GRAYSCALE)
        roi = (cv2.resize(mk, (W, H), interpolation=cv2.INTER_NEAREST) > 127).astype(np.uint8)
        zone = dilate(roi, buffer_px(gsd, asset.get("buffer_m", 1.0), 3, (H, W)))
        method = f"manual mask ({Path(mask_path).name})"
    elif kind == "pv":
        roi, zone, extra = pv_array_roi(bgr, gsd, rc, cfg["pv"], scene)
        method = "auto: PV modules verified by rectangularity + periodic cell grid"
    elif kind in ("structure", "yard"):
        roi = structure_roi(scene, rc, rc["structure_min_pct"], (H, W))
        method = "auto: structure surfaces (low-saturation concrete/steel/masonry; sky, vegetation, water, vehicles removed)"
        if kind == "structure" and asset.get("earthen_fallback") and roi.mean() * 100 < rc["structure_min_pct"] * 2:
            roi = ((scene["sky"] == 0) & (scene["water"] == 0) & (scene["vehicle"] == 0)).astype(np.uint8)
            method = "auto: earthen embankment face (frame minus sky/water/vehicles)"
        zone = dilate(roi, buffer_px(gsd, asset.get("buffer_m", 0.5), 1.5, (H, W))) if asset.get("buffer_m", 0.5) > 0 else roi.copy()
        if asset.get("zone_frame"):        # embankments: the whole visible earth structure (slopes, toe) minus water/sky
            zone = ((scene["water"] == 0) & (scene["sky"] == 0)).astype(np.uint8)
    elif kind == "pavement":
        p = ((scene["hsv"][..., 1] < rc["grey_s_max"]) & (scene["hsv"][..., 2] >= 20) & (scene["hsv"][..., 2] <= 215)).astype(np.uint8)
        p[(scene["veg"] > 0) | (scene["sky"] > 0) | (scene["water"] > 0)] = 0
        p = cv2.morphologyEx(p, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
        p = keep_components(p, lambda st, i, lab: st[4] >= rc["pavement_min_pct"] / 100.0 * H * W)
        roi = fill_holes(p, int(0.05 * H * W))
        roi[scene["vehicle"] > 0] = 0
        zone = dilate(roi, buffer_px(gsd, asset.get("buffer_m", 1.0), 2, (H, W)))
        method = "auto: carriageway/shoulder (grey/dark pavement; vehicles, verges and fields excluded)"
    elif kind == "canal":
        lin = structure_roi(scene, rc, 1.0, (H, W))
        lin = keep_components(lin, lambda st, i, lab: elongated(st, i, lab, H, W))
        wat = keep_components(scene["water"], lambda st, i, lab: elongated(st, i, lab, H, W))
        prism = fill_holes(cv2.morphologyEx(lin | wat, cv2.MORPH_CLOSE, np.ones((25, 25), np.uint8)), int(0.1 * H * W))
        roi = lin if lin.any() else prism
        zone = dilate(prism, buffer_px(gsd, asset.get("buffer_m", 8.0), 8, (H, W)))
        extra["prism"] = prism
        method = "auto: canal prism (elongated lining/water) + berm buffer"
    elif kind == "tower":
        roi = lattice_roi(scene, rc, (H, W))
        zone = np.ones((H, W), np.uint8)    # corridor = framed scene; only woody vegetation counts
        method = "auto: lattice tower/gantry members; corridor = frame (woody vegetation only)"
    elif kind == "linear":
        roi = linear_structure_roi(bgr, scene, rc, gsd)
        zone = dilate(roi, buffer_px(gsd, asset.get("buffer_m", 3.0), 5, (H, W)))
        method = "auto: elongated linear structure (pipe) + RoW buffer"
    elif kind == "facade":
        roi = ((scene["sky"] == 0) & (scene["water"] == 0) & (scene["vehicle"] == 0) & (scene["veg"] == 0)).astype(np.uint8)
        roi = keep_components(cv2.morphologyEx(roi, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8)),
                              lambda st, i, lab: st[4] >= 0.1 * H * W)
        roi = fill_holes(roi, int(0.1 * H * W))
        zone = dilate(roi, 3)
        method = "auto: building envelope (frame minus sky, vegetation, water, vehicles)"
    else:
        roi = np.ones((H, W), np.uint8)
        zone = roi
        method = "full frame (no asset segmentation defined)"
    cover = 100.0 * roi.mean()
    ce = rc["core_erode_px"] or (cfg["thresholds"]["crack"]["blackhat_kernel_px"] // 2 + 3)
    core = erode(roi, ce)
    return {"roi": roi.astype(np.uint8), "core": core.astype(np.uint8), "zone": zone.astype(np.uint8),
            "method": method, "cover_pct": cover, "extra": extra}


def on_structure_vegetation(veg, roi, ring_px, min_frac, opening_min_side_px=None, opening_rect=0.9):
    """Vegetation counts as growing ON the asset (deck, joints, piers, facade, pavement) only when its
    surrounding ring is mostly asset surface. Ring pixels outside the frame count as NOT asset, so
    fields/verges running out of the frame between piers or beside a road are rejected.
    Background land seen THROUGH an opening framed by members (between girders, below the deck soffit and
    above a pier cap) is also ringed by asset: it is recognised by its outline - a large near-perfect
    rectangle cut by straight member edges - whereas plants/moss have irregular outlines."""
    Hh, Ww = veg.shape
    roi_p = np.pad(roi.astype(np.uint8), ring_px + 2)

    def ok(st, i, lab):
        x, y, w, h, a = st
        pad = ring_px + 2
        comp = np.zeros((h + 2 * pad, w + 2 * pad), np.uint8)
        comp[pad:pad + h, pad:pad + w] = lab[y:y + h, x:x + w] == i
        ring = dilate(comp, ring_px) & (1 - comp)
        r = roi_p[y:y + h + 2 * pad, x:x + w + 2 * pad]
        if x == 0 or y == 0 or x + w >= Ww or y + h >= Hh:   # runs out of frame -> land, not growth on asset
            return False
        if opening_min_side_px and min(w, h) >= opening_min_side_px:
            rr, rect = rectangularity(comp)
            if rect is not None and rr >= opening_rect and min(rect[1]) >= opening_min_side_px:
                return False                                  # view through a framed opening
        return ring.sum() > 0 and (ring & r).sum() / ring.sum() >= min_frac
    return keep_components(veg, ok)


def is_straight_line(cr, shape, rc):
    """Construction joints, member edges, formwork lines, shadow edges: long and ~perfectly straight.
    The 8-connected skeleton length of a perfectly straight digital line is chord x (max(|cos|,|sin|) +
    (sqrt2-1) min(|cos|,|sin|)), up to 8 % longer than the chord at 22.5 deg - normalise by it, otherwise only
    lines within ~4 deg of 0/45/90 deg can ever pass the 3 % tortuosity test."""
    ln = cr["length_px"]
    a = math.radians(cr.get("angle", 0.0))
    ca, sa = abs(math.cos(a)), abs(math.sin(a))
    digital = max(ca, sa) + (math.sqrt(2) - 1) * min(ca, sa)
    if ln >= rc["straight_line_min_frac"] * max(shape) and \
            ln / max(cr["chord_px"] * digital, 1.0) < rc["straight_line_tortuosity"]:
        return True
    # straight BAND (joint gap, member edge, bar shadow) cut short by a lane line / plant / vehicle, whose skeleton
    # is ragged: all its pixels lie within its own thickness (+1 % of length) of one straight line
    if cr["chord_px"] >= min(rc.get("straight_band_min_frac", 0.1) * max(shape), rc.get("straight_band_min_px", 150)):
        x, y, comp = cr["mask"]
        ys, xs = np.nonzero(comp)
        pts = np.vstack([xs, ys]).astype(np.float64)
        pts -= pts.mean(1, keepdims=True)
        _, evec = np.linalg.eigh(np.cov(pts))
        across = float(np.ptp(evec[:, 0] @ pts))
        thick = len(xs) / max(cr["chord_px"], 1.0)
        return across <= thick + max(2.0, 0.01 * cr["chord_px"])
    return False


def project_to_ground(md, cx, cy, W, H, gsd_mm):
    """Defect ground position from camera GPS + pixel offset (near-nadir, yaw known). Else camera position."""
    lat, lon = md.get("lat"), md.get("lon")
    if lat is None or lon is None:
        return None, None, "none"
    pitch, yaw = md.get("gimbal_pitch"), md.get("gimbal_yaw")
    if gsd_mm and yaw is not None and pitch is not None and abs(pitch) >= 70:
        dx = (cx - W / 2.0) * gsd_mm / 1000.0
        dy = (H / 2.0 - cy) * gsd_mm / 1000.0
        a = math.radians(yaw)
        e = dx * math.cos(a) + dy * math.sin(a)
        n = -dx * math.sin(a) + dy * math.cos(a)
        return lat + n / 111320.0, lon + e / (111320.0 * math.cos(math.radians(lat))), "projected"
    return lat, lon, "camera"


# =============================================================================
# 7. THERMAL
# =============================================================================
_PAL_CACHE = {}


def palette_lut(name):
    """256x3 BGR lookup approximating common camera palettes."""
    i = np.arange(256, dtype=np.float32)
    if name == "whitehot":
        rgb = np.stack([i, i, i], 1)
    elif name == "blackhot":
        rgb = np.stack([255 - i] * 3, 1)
    elif name == "rainbow":
        return cv2.applyColorMap(np.arange(256, dtype=np.uint8).reshape(-1, 1), cv2.COLORMAP_JET).reshape(256, 3).astype(np.float32)
    else:  # ironbow
        cp = [(0.0, (0, 0, 0)), (0.15, (40, 0, 110)), (0.30, (120, 0, 150)), (0.45, (190, 20, 120)),
              (0.60, (230, 70, 40)), (0.75, (250, 140, 0)), (0.90, (255, 215, 40)), (1.0, (255, 255, 255))]
        xs = [p[0] * 255 for p in cp]
        rgb = np.stack([np.interp(i, xs, [p[1][c] for p in cp]) for c in range(3)], 1)
    return rgb[:, ::-1].astype(np.float32)  # -> BGR


def palette_to_temperature(bgr, palette, tmin, tmax):
    if palette not in _PAL_CACHE:
        lut = palette_lut(palette)
        q = np.arange(32, dtype=np.float32) * 8 + 4
        B, G, R = np.meshgrid(q, q, q, indexing="ij")
        cols = np.stack([B.ravel(), G.ravel(), R.ravel()], 1)          # 32768 x 3
        d = ((cols[:, None, :] - lut[None, :, :]) ** 2).sum(2)          # 32768 x 256
        _PAL_CACHE[palette] = d.argmin(1).astype(np.float32)
    table = _PAL_CACHE[palette]
    qb = (bgr >> 3).astype(np.int32)
    idx = (qb[..., 0] << 10) | (qb[..., 1] << 5) | qb[..., 2]
    return tmin + table[idx] / 255.0 * (tmax - tmin)


def crop_pct(img, pct):
    t, b, l, r = pct
    H, W = img.shape[:2]
    return img[int(H * t / 100): H - int(H * b / 100), int(W * l / 100): W - int(W * r / 100)]


def load_thermal(path, cfg, bgr=None):
    """Return (T degC float32 2D, source description, radiometric bool)."""
    tc = cfg["thermal"]
    ext = Path(path).suffix.lower()
    if bgr is None:
        if ext in (".csv", ".txt"):
            txt = Path(path).read_text(encoding="utf-8", errors="ignore")
            delim = "," if txt.count(",") > txt.count(";") else (";" if ";" in txt else None)
            return np.loadtxt(path, delimiter=delim, dtype=np.float32), "temperature matrix", True
        if ext in (".tif", ".tiff"):
            arr = imread_any(path, cv2.IMREAD_UNCHANGED)
            if arr is not None and arr.ndim == 2:
                if arr.dtype.kind == "f":
                    return arr.astype(np.float32), "radiometric TIFF (float, degC)", True
                T = arr.astype(np.float32) * tc["tiff_uint16_scale"] + tc["tiff_uint16_offset"]
                return T, "radiometric TIFF (int, scaled)", True
        if ext in (".jpg", ".jpeg") and tc.get("dji_irp_path") and Path(tc["dji_irp_path"]).exists():
            img = imread_any(path)
            h, w = img.shape[:2]
            with tempfile.TemporaryDirectory() as td:
                out = Path(td) / "t.raw"
                cmd = [tc["dji_irp_path"], "-s", str(path), "-a", "measure", "-o", str(out),
                       "--measurefmt", "float32"] + list(tc.get("dji_irp_extra_args") or [])
                r = subprocess.run(cmd, capture_output=True, timeout=120)
                if out.exists() and out.stat().st_size == h * w * 4:
                    return np.fromfile(str(out), np.float32).reshape(h, w), "DJI R-JPEG (dji_irp)", True
                print("dji_irp failed:", r.stderr[-300:])
        bgr = imread_any(path)
    if bgr is None:
        raise ValueError("unreadable thermal file")
    bgr = crop_pct(bgr, tc["palette_crop_pct"])
    T = palette_to_temperature(bgr, tc["palette"], float(tc["palette_tmin_c"]), float(tc["palette_tmax_c"]))
    return T.astype(np.float32), f"palette image ({tc['palette']}, {tc['palette_tmin_c']}-{tc['palette_tmax_c']} C) - indicative", False


def colorize_T(T):
    lo, hi = np.nanpercentile(T, 1), np.nanpercentile(T, 99.5)
    n = np.clip((T - lo) / max(hi - lo, 1e-3) * 255, 0, 255).astype(np.uint8)
    return cv2.applyColorMap(n, cv2.COLORMAP_INFERNO)


def thermal_components(mask, Tdelta, T, min_px):
    n, lab, st, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    comps = []
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if a < min_px:
            continue
        sel = lab == i
        dv = Tdelta[sel]
        k = np.argmax(np.abs(dv))
        comps.append({"bbox": [int(x), int(y), int(w), int(h)], "area_px": int(a),
                      "dT": float(dv[k]), "Tpeak": float(T[sel][k]), "aspect": max(w, h) / max(1, min(w, h))})
    comps.sort(key=lambda c: -abs(c["dT"]))
    return comps


def thermal_roi(Ts, mode, cfg, gsd_mm, given=None):
    """Thermal analysis zone. given = mask transferred from the paired visual image or a manual mask.
    Always removes: frame border (uncooled-sensor vignetting), sky (very cold, top-connected).
    PV: verified array = large, near-rectangular, thermally uniform module blocks (holes up to ~1.5
    modules filled so hot cells/modules stay inside; vehicles, roofs, roads are outside).
    Seepage: open water (large, uniform, cold) removed."""
    rc = cfg["roi"]
    H, W = Ts.shape
    b = int(rc["thermal_border_pct"] / 100.0 * min(H, W))
    valid = np.zeros((H, W), np.uint8)
    valid[b:H - b, b:W - b] = 1
    med = float(np.median(Ts))
    # sky: very cold; also patches seen below a deck / between members (not connected to the top edge)
    sky = keep_components((Ts < med - 10).astype(np.uint8), lambda st, i, lab: st[4] > (0.02 if st[1] == 0 else 0.005) * H * W)
    valid &= (1 - sky)
    if given is not None:
        return (given.astype(np.uint8) & valid), "transferred from paired visual image / manual mask"
    if mode == "pv":
        pv = cfg["pv"]
        mod_px = pv["module_length_m"] * pv["module_width_m"] * (1000.0 / gsd_mm) ** 2 if gsd_mm else None
        ls = local_std(cv2.medianBlur(Ts.astype(np.float32), 5), 7)   # module frames / gaps (1-2 px lines) don't split blocks
        thr = thr0 = rc["thermal_pv_uniform_std_c"]
        lg = np.log(ls[valid > 0] + 0.02)          # modules (glass, uniform) vs ground (textured): split if bimodal
        lg = lg[(lg >= np.percentile(lg, 2)) & (lg <= np.percentile(lg, 90))]   # drop edge/outlier texture
        if lg.size > 100:
            q = np.clip((lg - lg.min()) / max(np.ptp(lg), 1e-6) * 255, 0, 255).astype(np.uint8)
            t8, _ = cv2.threshold(q, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            t = lg.min() + t8 / 255.0 * np.ptp(lg)
            lo_m, hi_m = lg[lg <= t].mean() if (lg <= t).any() else 0, lg[lg > t].mean() if (lg > t).any() else 0
            if hi_m - lo_m > 0.6:                   # clearly two texture classes
                thr = min(thr, float(np.exp(t) - 0.02))
        min_a = 2 * mod_px if mod_px else 0.01 * H * W

        def blocks(t, min_rect):
            uni = cv2.morphologyEx((ls < t).astype(np.uint8) & valid, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
            return keep_components(uni, lambda st, i, lab: st[4] >= min_a and
                                   rectangularity(lab[st[1]:st[1] + st[3], st[0]:st[0] + st[2]] == i)[0] >= min_rect)
        arr = blocks(thr, 0.5)
        how = "auto: PV array (uniform rectangular module blocks, hull-filled)"
        if gsd_mm:
            # PV evidence = module frames / gaps repeating at the module pitch. Cast shadows, roads, roofs, vehicle
            # bodies and grass are smooth rectangles too (often smoother than modules, so the adaptive threshold
            # alone selects them); test blocks at the adaptive and at nominal thresholds (a stricter one detaches
            # tables from ground of similar temperature).
            lat = np.zeros((H, W), np.uint8)
            unver = []
            for t in {thr, thr0, 0.75 * thr0}:
                bl = blocks(t, rc["thermal_pv_table_min_rect"])
                ok = keep_components(bl, lambda st, i, lab: module_lattice(
                    grid_periodicity(Ts, lab == i, rectangularity(lab == i)[1], per_axis=True), gsd_mm, rc))
                lat |= ok
                unver.append(bl & (1 - ok))
            if lat.any():
                # row completion: a uniform rectangular block with the SAME table depth and orientation lying in the
                # row of a verified table is a table too, even when its grid is washed out (e.g. an offline string
                # running uniformly warm has little frame contrast)
                refs = []
                t_mod = float(np.median(Ts[lat > 0]))
                for c_ in cv2.findContours(lat, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]:
                    (cx, cy), (rw, rh), an = cv2.minAreaRect(c_)
                    if rw < rh:
                        rw, rh, an = rh, rw, an + 90.0
                    refs.append((cx, cy, rw, rh, math.radians(an)))
                for bl in unver:
                    for c_ in cv2.findContours(bl, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]:
                        (cx, cy), (rw, rh), an = cv2.minAreaRect(c_)
                        if rw < rh:
                            rw, rh, an = rh, rw, an + 90.0
                        cm = np.zeros((H, W), np.uint8)
                        cv2.drawContours(cm, [c_], -1, 1, -1)
                        if abs(float(np.median(Ts[cm > 0])) - t_mod) > rc["thermal_pv_row_max_dT"]:
                            continue                     # roofs / sheds in line with a row are far hotter than modules
                        for (qx, qy, qw, qh, qa) in refs:
                            dang = abs(((math.degrees(qa) - an) + 90.0) % 180.0 - 90.0)
                            perp = abs(-(cx - qx) * math.sin(qa) + (cy - qy) * math.cos(qa))
                            same_depth = abs(rh - qh) <= 0.2 * qh or abs(2 * rh - qh) <= 0.2 * qh   # table or one module row
                            if dang < 5 and same_depth and perp <= 0.5 * qh:
                                cv2.drawContours(lat, [c_], -1, 1, -1)
                                break
                arr, how = lat, how + "; module grid verified"
            else:
                how += "; module grid not resolved - unverified"
        # each table/block -> its convex hull, so anomalous modules at a block edge stay inside the array
        out = np.zeros((H, W), np.uint8)
        cnts, _ = cv2.findContours(arr, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in cnts:
            cv2.fillPoly(out, [cv2.convexHull(c)], 1)
        return out & valid, how
    if mode in ("seepage_cold", "civil_warm"):    # open water is never the asset (river under a bridge, canal)
        cold = ((Ts < med - 1.0) & (local_std(Ts, 9) < rc["thermal_water_max_std_c"])).astype(np.uint8)
        water = dilate(keep_components(cold, lambda st, i, lab: st[4] > 0.1 * H * W), 8)   # incl. shoreline
        return valid & (1 - water), "auto: frame minus border, sky and open water"
    return valid, "auto: frame minus border and sky"


def merge_adjacent(comps, gap_px, max_ddT):
    """One fault seen through module frames / row gaps appears as several touching patches with the same dT
    (open string, PID patchwork): merge patches whose boxes are within gap_px and dT differs <= max_ddT."""
    comps = [dict(c) for c in comps]
    merged = True
    while merged:
        merged = False
        for i in range(len(comps)):
            for j in range(i + 1, len(comps)):
                a, b = comps[i], comps[j]
                ax0, ay0, aw, ah = a["bbox"]
                bx0, by0, bw, bh = b["bbox"]
                if (bx0 > ax0 + aw + gap_px or ax0 > bx0 + bw + gap_px or by0 > ay0 + ah + gap_px or ay0 > by0 + bh + gap_px
                        or abs(a["dT"] - b["dT"]) > max_ddT):
                    continue
                x0, y0 = min(ax0, bx0), min(ay0, by0)
                x1, y1 = max(ax0 + aw, bx0 + bw), max(ay0 + ah, by0 + bh)
                top = a if abs(a["dT"]) >= abs(b["dT"]) else b
                comps[i] = {**top, "bbox": [x0, y0, x1 - x0, y1 - y0], "area_px": a["area_px"] + b["area_px"],
                            "aspect": max(x1 - x0, y1 - y0) / max(1, min(x1 - x0, y1 - y0))}
                del comps[j]
                merged = True
                break
            if merged:
                break
    comps.sort(key=lambda c: -abs(c["dT"]))
    return comps


def analyze_thermal(T, mode, cfg, gsd_mm, given_roi=None):
    """Returns (list of anomaly dicts with severity, stats dict). gsd_mm: thermal GSD or None.
    Anomalies are searched ONLY inside the thermal ROI (see thermal_roi)."""
    tc, th, env = cfg["thermal"], cfg["thresholds"], cfg["environment"]
    T = np.where(np.isfinite(T), T, np.nanmedian(T)).astype(np.float32)
    Ts = cv2.GaussianBlur(T, (0, 0), tc["smoothing_sigma"]) if tc["smoothing_sigma"] > 0 else T
    H, W = Ts.shape
    stats = {"Tmin": float(Ts.min()), "Tmax": float(Ts.max()), "Tmed": float(np.median(Ts))}
    minpx = int(tc["min_anomaly_px"])
    out = []
    roi, how = thermal_roi(Ts, mode, cfg, gsd_mm, given_roi)
    stats.update(roi_method=how, roi_cover_pct=round(100.0 * roi.mean(), 1))
    if mode == "pv" and stats["roi_cover_pct"] < cfg["roi"]["min_coverage_pct"]:
        stats["no_asset"] = True
        if cfg["roi"]["strict"]:
            return out, stats
        roi = np.ones((H, W), np.uint8)
    stats["roi_mask"] = roi
    if mode == "electrical":
        bg = float(np.median(Ts))
        fg = ((Ts > bg + tc["electrical_fg_delta_c"]) & (roi > 0)).astype(np.uint8)
        # bulk sun-heated surfaces (ground, roofs, vehicles bodies) are not electrical equipment
        fg = keep_components(fg, lambda st, i, lab: st[4] <= cfg["roi"]["thermal_equipment_max_pct"] / 100.0 * H * W) > 0
        if fg.sum() < minpx:
            return out, stats
        ref = float(np.median(Ts[fg]))                      # similar-component reference
        mad = float(np.median(np.abs(Ts[fg] - ref))) * 1.4826
        thr = max(tc["electrical_min_report_c"], 3 * mad)
        comps = thermal_components(fg & (Ts > ref + thr), Ts - ref, Ts, minpx)
        load, nexp, amb = env.get("load_pct"), env.get("load_exponent", 2.0), env.get("ambient_temp_c")
        corr = (100.0 / load) ** nexp if load and 0 < load < 100 else 1.0
        for cpt in comps[:10]:
            dT = cpt["dT"] * corr
            sev = band(dT, th["thermal_electrical_c2c"])
            d_amb = None
            if amb is not None:
                d_amb = (cpt["Tpeak"] - amb) * corr
                s2 = band(d_amb, th["thermal_electrical_amb"])
                if s2 and (not sev or SEV_RANK[s2] > SEV_RANK[sev]):
                    sev = s2
            if not sev:
                continue
            cpt.update(severity=sev, ref=ref, dT_corr=dT, dT_amb=d_amb, load_corr=corr,
                       pattern="component-to-component hot-spot")
            out.append(cpt)
        stats["ref"] = ref
        return out, stats
    if mode == "pv":
        vals = Ts[roi > 0]
        lo, hi = np.percentile(vals, [10, 90])
        core = vals[(vals >= lo) & (vals <= hi)]
        ref = float(np.median(core))                                        # array reference (modules only)
        noise = float(np.median(np.abs(core - ref))) * 1.4826 * 1.6        # trimmed MAD -> module-to-module spread
        stats["module_sigma"] = round(noise, 2)
        bands = th["thermal_pv"]
        hot = (((Ts - ref) > bands[0][0]) & (roi > 0)).astype(np.uint8)
        if gsd_mm:   # cooler module frames / inter-module gaps (~0.1 m) must not split a hot string / PID patchwork
            k = 2 * max(1, int(round(100.0 / gsd_mm))) + 1
            hot = cv2.morphologyEx(hot, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8)) & (roi > 0)
        comps = merge_adjacent(thermal_components(hot, Ts - ref, Ts, minpx),
                               max(2, int(round(300.0 / gsd_mm))) if gsd_mm else 4, 2.0)
        pv = cfg["pv"]
        mod_area = pv["module_length_m"] * pv["module_width_m"]
        for cpt in comps[:25]:
            sev = band(cpt["dT"], bands)
            if not sev:
                continue
            pat = "unclassified (no thermal GSD)"
            if gsd_mm:
                area_m2 = cpt["area_px"] * (gsd_mm / 1000.0) ** 2
                r = area_m2 / mod_area
                cpt["area_m2"] = area_m2
                if r < 0.12:
                    pat = "cell"
                elif r < 0.6:
                    pat = "substring"
                elif r < 1.6:
                    pat = "module"
                elif r < 0.6 * pv["modules_per_string"]:
                    pat = "multi_module"
                else:
                    pat = "string"
                cpt["modules_affected"] = max(1, round(r)) if pat in ("multi_module", "string") else 1
                # single-cell differences of a few K are normal cell mismatch / soiling specks: a cell hot-spot
                # must clear both an absolute floor and the array's own temperature spread
                if pat == "cell" and cpt["dT"] < max(tc["pv_cell_min_dT"], tc["pv_noise_k"] * noise):
                    continue
                if pat in ("string", "multi_module"):   # energy-significant patterns
                    sev = max(sev, "High" if pat == "string" else "Medium", key=lambda s: SEV_RANK[s])
            cpt.update(severity=sev, ref=ref, pattern=pat)
            out.append(cpt)
        stats["ref"] = ref
        return out, stats
    # civil / seepage / building / pipeline : local anomaly vs smoothed background
    sig = max(5.0, tc["local_bg_fraction"] * max(H, W))
    wgt = roi.astype(np.float32)                 # background from ROI pixels only (normalised convolution)
    bgm = cv2.GaussianBlur(Ts * wgt, (0, 0), sig) / np.maximum(cv2.GaussianBlur(wgt, (0, 0), sig), 1e-3)
    d = Ts - bgm
    bands = th[THERMAL_MODES[mode][1]]
    # detection threshold adapts to the surface's own thermal texture (asphalt/concrete mottling, sensor noise):
    # an anomaly must exceed both the lowest band and k x robust spread of the residual inside the ROI
    # spread measured on the surface itself, away from member edges / sun-shadow lines (strong gradients)
    gm = cv2.magnitude(cv2.Sobel(Ts, cv2.CV_32F, 1, 0), cv2.Sobel(Ts, cv2.CV_32F, 0, 1)) / 8.0
    flat = (roi > 0) & (dilate((gm > tc["civil_edge_grad_c_px"]).astype(np.uint8), int(sig)) == 0)
    dv = d[flat] if flat.sum() > 200 else d[roi > 0]
    spread = float(np.median(np.abs(dv - np.median(dv)))) * 1.4826 if dv.size else 0.0
    stats["residual_sigma_c"] = round(spread, 2)
    t0 = max(bands[0][0], tc["civil_noise_k"] * spread)
    if mode == "civil_warm":
        mask = d > t0
    elif mode == "seepage_cold":
        mask = d < -t0
    else:
        mask = np.abs(d) > t0
    mask = mask & (roi > 0)
    mask = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    # a defect is warmer (colder) than its whole surroundings; one side of a sun/shadow boundary or a deck
    # edge next to cool water / sky only differs from the smoothed background, not from its own ring
    rw = max(3, int(0.02 * max(H, W)))
    gap = max(2, rw // 2)

    def ring_contrast_ok(st, i, lab):
        x, y, w, h, a = st
        p = gap + rw + 1
        x0, y0, x1, y1 = max(0, x - p), max(0, y - p), min(W, x + w + p), min(H, y + h + p)
        comp = (lab[y0:y1, x0:x1] == i).astype(np.uint8)
        ring = (dilate(comp, gap + rw) > 0) & (dilate(comp, gap) == 0) & (roi[y0:y1, x0:x1] > 0)
        if ring.sum() < 8:
            return True
        t = Ts[y0:y1, x0:x1]
        sgn = 1.0 if float(np.mean(d[y0:y1, x0:x1][comp > 0])) > 0 else -1.0
        q = np.percentile(t[ring], 90 if sgn > 0 else 10)
        return sgn * (float(np.median(t[comp > 0])) - q) > 0.5 * t0
    mask = keep_components(mask, ring_contrast_ok)
    comps = thermal_components(mask, d, Ts, max(minpx, int(0.0005 * H * W)))
    for cpt in comps[:15]:
        sev = band(abs(cpt["dT"]), bands)
        if not sev:
            continue
        pct = 100.0 * cpt["area_px"] / (H * W)
        if mode == "civil_warm" and pct > tc["civil_max_anomaly_pct"]:
            # a frame-scale warm region is solar loading / sun-shadow pattern, not a delamination
            stats["large_gradient_not_assessed"] = stats.get("large_gradient_not_assessed", 0) + 1
            continue
        if pct > th["thermal_area_escalate_pct"]:
            sev = escalate(sev)
        cpt.update(severity=sev, area_pct=pct, pattern="warm" if cpt["dT"] > 0 else "cold")
        if gsd_mm:
            cpt["area_m2"] = cpt["area_px"] * (gsd_mm / 1000.0) ** 2
        out.append(cpt)
    return out, stats


# =============================================================================
# 8. ANALYSIS ENGINE
# =============================================================================
@dataclass
class Finding:
    fid: str
    defect: str                 # KB key
    severity: str
    summary: str
    source: str                 # relative path [+ @t for video]
    metrics: dict = field(default_factory=dict)
    lat: float = None
    lon: float = None
    confidence: str = "Medium"
    quantity: dict = field(default_factory=dict)   # {"length_m":..} or {"area_m2":..}
    thumb: str = ""             # base64 jpg crop
    group: str = ""             # location group: same defect seen in overlapping frames


class Engine:
    def __init__(self, cfg, asset_key, in_dir, out_dir, project, log=print, progress=None, stop_event=None):
        self.cfg, self.asset_key = cfg, asset_key
        self.asset = all_assets(cfg)[asset_key]
        self.in_dir, self.out_dir = Path(in_dir), Path(out_dir)
        self.project = project
        self.log = log
        self.progress = progress or (lambda f, msg="": None)
        self.stop = stop_event or threading.Event()
        self.findings, self.media = [], []
        self.n = 0
        self.roi_cache = {}          # visual image path -> (roi, zone, (W, H)) for thermal pairing
        self.cap_conf = None         # confidence cap for the frame being analysed
        self.cur_geo = None          # (W, H, gsd_mm, md) of the frame being analysed

    # ---------------------------------------------------------------- scan
    def scan(self):
        p = self.cfg["processing"]
        rgb, thermal, video = [], [], []
        tok = [t.upper() for t in p["thermal_name_tokens"]]
        for f in sorted(self.in_dir.rglob("*")):
            if not f.is_file() or self.out_dir in f.parents:
                continue
            ext = f.suffix.lower()
            up = str(f).upper()
            if ext in p["video_ext"]:
                video.append(f)
            elif ext in p["thermal_matrix_ext"] and ext != ".raw":
                thermal.append(f)
            elif ext in p["image_ext"]:
                if any(t in up for t in tok):
                    thermal.append(f)
                    continue
                try:
                    with Image.open(f) as im:
                        size, mode = list(im.size), im.mode
                except Exception:
                    continue
                if ext in (".tif", ".tiff") and mode in ("I;16", "I;16B", "I", "F"):
                    thermal.append(f)
                elif size in p["thermal_resolutions"]:
                    thermal.append(f)
                else:
                    rgb.append(f)
        return rgb, thermal, video

    def rel(self, f):
        try:
            return str(Path(f).relative_to(self.in_dir))
        except ValueError:
            return Path(f).name

    def add(self, defect, severity, summary, src, md, metrics, img, bbox, conf="Medium", qty=None):
        self.n += 1
        fid = f"F{self.n:04d}"
        thumb = ""
        if img is not None and bbox:
            x, y, w, h = bbox
            H, W = img.shape[:2]
            pad = max(40, int(0.6 * max(w, h)))
            crop = img[max(0, y - pad):min(H, y + h + pad), max(0, x - pad):min(W, x + w + pad)]
            if crop.size:
                thumb = b64_jpg(crop, self.cfg["processing"]["thumb_width_px"])
        elif img is not None:
            thumb = b64_jpg(img, self.cfg["processing"]["thumb_width_px"])
        lat, lon, how = md.get("lat"), md.get("lon"), "camera"
        if self.cur_geo and bbox:
            W, H, gsd, _ = self.cur_geo
            lat, lon, how = project_to_ground(md, bbox[0] + bbox[2] / 2, bbox[1] + bbox[3] / 2, W, H, gsd)
        metrics = dict(metrics, location_basis=how)
        if self.cap_conf and SEV_RANK.get(conf, 1) > 0:
            conf = self.cap_conf
        self.findings.append(Finding(fid, defect, severity, summary, src, metrics, lat, lon, conf, qty or {}, thumb))

    # ---------------------------------------------------------------- RGB
    def analyze_rgb(self, bgr, src, md, native_w, path=None):
        cfg, th, det = self.cfg, self.cfg["thresholds"], self.asset["detectors"]
        amap = self.asset.get("map", {})
        H0, W0 = bgr.shape[:2]
        # crack-centric assets need full resolution; mapping-scale assets (PV, corridors, yards) are processed
        # at a lighter resolution (their defects span many pixels; tuned detectors, 3-5x faster)
        side = self.asset.get("max_process_side_px", cfg["processing"]["max_process_side_px"])
        s = min(1.0, side / max(H0, W0))
        if s < 1:
            bgr = cv2.resize(bgr, (int(W0 * s), int(H0 * s)), interpolation=cv2.INTER_AREA)
        H, W = bgr.shape[:2]
        gsd0, basis = compute_gsd_mm(md, native_w, cfg)
        gsd = gsd0 * native_w / W if gsd0 else None
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        rec = {"source": src, "kind": "rgb", "w": native_w, "h": int(H0 * native_w / W0), "gsd_mm": gsd0,
               "gsd_basis": basis, "lat": md.get("lat"), "lon": md.get("lon"), "rel_alt": md.get("rel_alt"),
               "model": md.get("model"), "datetime": md.get("datetime"), "flags": [], "n_findings": 0}
        # --- image quality QA
        g1k = cv2.resize(gray, (1000, int(1000 * H / W))) if W > 1000 else gray
        blur = float(cv2.Laplacian(g1k, cv2.CV_64F).var())
        mean = float(gray.mean())
        rec.update(blur=blur, brightness=mean)
        p = cfg["processing"]
        if blur < p["blur_var_laplacian_min"]:
            rec["flags"].append("blurred")
        if mean < p["exposure_dark_mean"]:
            rec["flags"].append("under-exposed")
        if mean > p["exposure_bright_mean"]:
            rec["flags"].append("over-exposed")
        if "blurred" in rec["flags"] and p["skip_blurred"]:
            self.media.append(rec)
            return
        # --- asset region of interest: defects are searched ONLY on the asset (or its defined buffer)
        rc = cfg["roi"]
        scene = scene_masks(bgr, gsd, {**rc, "exg_min": th["vegetation"]["exg_min"],
                                       "grey_vehicles": self.asset.get("grey_vehicles", False)})
        R = asset_roi(self.asset, bgr, gsd, cfg, scene, find_manual_mask(path, self.in_dir, rc))
        roi, core, zone = R["roi"], R["core"], R["zone"]
        rec.update(roi_method=R["method"], roi_cover_pct=round(R["cover_pct"], 1))
        ann = bgr.copy()
        overlay = bgr.copy()
        n0 = len(self.findings)
        self.cap_conf = None
        min_cov = self.asset.get("min_coverage_pct", rc["min_coverage_pct"])
        if R["cover_pct"] < min_cov and self.asset.get("zone_independent"):
            roi = core = np.zeros((H, W), np.uint8)    # no member surface -> member detectors idle, corridor still assessed
            rec["flags"].append("asset members not identified - corridor-only analysis")
        elif R["cover_pct"] < min_cov:
            if rc["strict"]:
                rec["flags"].append("asset not identified in frame - not analysed (supply a manual mask to include)")
                rec["analysed"] = False
                self._finish_media(rec, ann, n0)
                return
            roi = core = zone = np.ones((H, W), np.uint8)
            rec["flags"].append("asset not delineated - full frame analysed (Low confidence)")
            self.cap_conf = "Low"
        rec["analysed"] = True
        if path:
            self.roi_cache[str(path)] = (roi, zone, (W, H))
        self.cur_geo = (W, H, gsd, md)
        excl = ((scene["vehicle"] > 0) | (scene["sky"] > 0) | (scene["water"] > 0)).astype(np.uint8)
        roi_px = max(int(roi.sum()), 1)
        pct_roi = lambda m: 100.0 * float(m.sum()) / roi_px
        area_m2 = lambda px: px * (gsd / 1000.0) ** 2 if gsd else None
        for m_, col in ((zone, (0, 255, 255)), (roi, (255, 255, 0))):        # zone yellow, ROI cyan outline
            cnts, _ = cv2.findContours(m_.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(ann, cnts, -1, col, 2)
        veg = scene["veg"]
        # --- PV: soiling & glass breakage on VERIFIED modules only
        if "solar" in det:
            panels = roi
            gmax = th["pv_glass"]["max_gsd_mm"]
            brk = []
            if gsd and gmax and gsd > gmax:
                rec["flags"].append(f"module glass breakage not assessed: GSD {gsd:.1f} mm/px coarser than {gmax:g} mm/px")
            else:
                brk = pv_glass_breakage(gray, panels, th["crack"], th["pv_glass"]["min_off_axis_frac"])
            pvc = cfg["pv"]
            mod_px = pvc["module_length_m"] * pvc["module_width_m"] * (1000.0 / gsd) ** 2 if gsd else None
            soil = soiling_mask(bgr, erode(panels, 2), th["soiling"], gsd, mod_px)
            for bb, _ in brk:                     # shattered glass is reported as breakage, not as soiling
                soil[bb[1]:bb[1] + bb[3], bb[0]:bb[0] + bb[2]] = 0
            spct = pct_roi(soil)
            sev = band(spct, th["soiling"]["bands_pct"]) if soil.any() else None
            if sev:
                overlay[soil > 0] = (0, 200, 255)
                self.add("soiling_pv", sev, f"Soiling / deposits on {spct:.1f}% of verified module area", src, md,
                         {"soiled_pct_of_modules": round(spct, 2), "patches": int(cv2.connectedComponents(soil)[0] - 1)},
                         ann, bbox_of(soil), "Medium", {"area_m2": area_m2(soil.sum())})
            if brk:
                for bb, j in brk[:10]:
                    cv2.rectangle(ann, (bb[0], bb[1]), (bb[0] + bb[2], bb[1] + bb[3]), (255, 0, 255), 2)
                b = max(brk, key=lambda t: t[1])[0]
                self.add("pv_glass_damage", "High", f"{len(brk)} branched, off-grid fracture pattern(s) on module glass", src, md,
                         {"patterns": len(brk)}, ann, b, "Medium", {"modules": len(brk)})
        sp = None
        if "spall" in det:          # computed first: a spall's broken perimeter is part of the spall, not a crack
            sp = spall_mask(gray, th["spall"], (veg | excl).astype(np.uint8),
                            buffer_px(gsd, th["spall"].get("min_thickness_m", 0.04), 1.0, (H, W)), gsd) & core
        # --- cracks: core of ROI; straight joints / edges / shadow lines rejected
        if "crack" in det:
            cr, _ = detect_cracks(gray, gsd, th["crack"], roi=core & (1 - dilate(veg, 2)),   # weeds/moss lines are not cracks
                                  straight_min_px=rc["straight_line_min_frac"] * max(H, W))
            n_lin = 0
            kept = []
            sp_zone = dilate(sp, buffer_px(gsd, 0.05, 0.5, (H, W))) if sp is not None else None
            for c in cr:
                x, y, comp = c["mask"]
                if is_straight_line(c, (H, W), rc):
                    n_lin += 1
                elif gsd and c["w_med_mm"] > th["crack"].get("max_crack_width_mm", 15.0):
                    n_lin += 1                                # wider than any crack: joint gap, kerb/barrier edge, shadow
                elif (comp & excl[y:y + comp.shape[0], x:x + comp.shape[1]]).sum() > 0.2 * comp.sum():
                    n_lin += 1
                elif sp is not None and sp.any() and (comp & sp_zone[y:y + comp.shape[0], x:x + comp.shape[1]]).sum() > 0.5 * comp.sum():
                    n_lin += 1                                # spall perimeter
                else:
                    kept.append(c)
            cr = kept
            if cr:
                cm = np.zeros((H, W), np.uint8)
                for c in cr:
                    x, y, comp = c["mask"]
                    cm[y:y + comp.shape[0], x:x + comp.shape[1]] |= comp
                key = amap.get("crack", "crack_concrete")
                bands = th["crack"]["pavement_width_bands_mm" if key == "crack_pavement" else "width_bands_mm"]
                worst = max(cr, key=lambda c: c["w_max_px"])
                tot_px = sum(c["length_px"] for c in cr)
                jd = 100.0 * sum(c["junctions"] for c in cr) / max(tot_px, 1)
                ori = [c["angle"] for c in cr]
                dom = np.median(ori)
                orient = "horizontal" if dom < 20 else ("vertical" if dom > 70 else "diagonal")
                pattern = "map / alligator (interconnected)" if (jd > th["crack"]["map_pattern_junctions_per_100px"] and len(cr) >= 4) \
                    else ("multiple parallel" if len(cr) >= 3 and np.std(ori) < 15 else ("multiple" if len(cr) > 1 else "isolated"))
                if gsd:
                    wmax = worst["w_max_mm"]
                    sev = band(wmax, bands, "Low")
                    summ = f"{len(cr)} crack(s), total {tot_px * gsd / 1000:.2f} m, max width ~{wmax:.2f} mm, {orient}, {pattern}"
                    metrics = {"count": len(cr), "total_length_m": round(tot_px * gsd / 1000, 3), "max_width_mm": round(wmax, 2),
                               "median_width_mm": round(float(np.median([c["w_med_mm"] for c in cr])), 2),
                               "orientation": orient, "pattern": pattern, "gsd_mm_px": round(gsd, 3)}
                    metrics["width_uncertainty_mm"] = round(gsd, 2)        # +/- 1 px at this GSD
                    if bands[1][0] > 0 and gsd > bands[1][0]:
                        metrics["note"] = f"GSD {gsd:.2f} mm/px coarser than {bands[1][0]} mm band limit: finer cracks not resolvable"
                    qty = {"length_m": tot_px * gsd / 1000}
                else:
                    sev = "Medium" if (tot_px > 0.5 * max(H, W) or pattern.startswith("map")) else "Low"
                    summ = f"{len(cr)} crack(s), total {tot_px:.0f} px, max width ~{worst['w_max_px']:.1f} px (no GSD), {orient}, {pattern}"
                    metrics = {"count": len(cr), "total_length_px": round(tot_px), "max_width_px": round(worst["w_max_px"], 1),
                               "orientation": orient, "pattern": pattern, "note": "width not quantified - set camera preset / GSD"}
                    qty = {}
                if n_lin:
                    metrics["straight_linear_features_rejected"] = n_lin
                if pattern.startswith("map") and SEV_RANK[sev] < 1:
                    sev = "Medium"
                if key == "crack_concrete" and orient == "diagonal" and SEV_RANK[sev] >= 1:
                    metrics["diagnostic_hint"] = "diagonal cracking - check for shear distress near supports"
                overlay[cm > 0] = (0, 0, 255)
                b = worst["bbox"]
                cv2.rectangle(ann, (b[0], b[1]), (b[0] + b[2], b[1] + b[3]), (0, 0, 255), 2)
                self.add(key, sev, summ, src, md, metrics, ann, b, "Medium", qty)
        # --- corrosion: only on the asset surface (soil, roofs, vehicles excluded)
        rust = None
        if "corrosion" in det:
            c = th["corrosion"]
            rust = color_mask_hsv(bgr, c["hsv_lo"], c["hsv_hi"]) & roi & (1 - excl) & (1 - veg)
            pct = pct_roi(rust)
            if pct >= c["min_area_pct"]:
                ri = sum(pct >= x for x in c["ri_limits_pct"])
                sev = c["ri_severity"][ri]
                overlay[rust > 0] = (0, 140, 255)
                self.add(amap.get("corrosion", "corrosion_steel"), sev,
                         f"Rust-coloured area {pct:.2f}% of asset surface in frame -> ISO 4628-3 Ri{ri}", src, md,
                         {"rust_area_pct_of_asset": round(pct, 3), "iso4628_3": f"Ri{ri}"}, ann, bbox_of(rust), "Medium",
                         {"area_m2": area_m2(rust.sum())})
        # --- efflorescence
        if "efflorescence" in det:
            ef = efflorescence_mask(bgr, th["efflorescence"]) & roi & (1 - excl)
            ef = mask_components(ef, max(30, int(th["efflorescence"]["min_area_m2"] * (1000.0 / gsd) ** 2) if gsd else 30))
            pct = pct_roi(ef)
            sev = band(pct, th["efflorescence"]["bands_pct"]) if ef.any() else None
            if sev:
                overlay[ef > 0] = (255, 255, 0)
                self.add("efflorescence", sev, f"White deposits / leaching {pct:.2f}% of asset surface", src, md,
                         {"area_pct_of_asset": round(pct, 3)}, ann, bbox_of(ef), "Low", {"area_m2": area_m2(ef.sum())})
        # --- spalling / pothole
        if "spall" in det:
            pct = pct_roi(sp)
            if sp.any():
                sev = band(pct, th["spall"]["bands_pct"], "Medium")
                if rust is not None and (dilate(sp, 7) & rust).any():
                    sev = escalate(sev)                       # spall + rust = exposed corroding rebar
                overlay[sp > 0] = (255, 0, 255)
                bb = bbox_of(sp)
                cv2.rectangle(ann, (bb[0], bb[1]), (bb[0] + bb[2], bb[1] + bb[3]), (255, 0, 255), 2)
                self.add(amap.get("spall", "spalling"), sev, f"Dark irregular void/spall-like regions {pct:.2f}% of asset surface", src, md,
                         {"area_pct_of_asset": round(pct, 3)}, ann, bb, "Low", {"area_m2": area_m2(sp.sum())})
        # --- vegetation: asset-specific rule (never the surrounding land)
        if "vegetation" in det:
            rule = self.asset.get("veg_rule", "on_structure")
            if rule == "on_structure":
                ring = int(np.clip(buffer_px(gsd, 0.1, 0.6, (H, W)), 3, 0.01 * min(H, W) + 3))   # thin ring, any GSD
                vm = on_structure_vegetation(veg, roi, ring, rc["on_structure_ring_frac"],
                                             buffer_px(gsd, rc.get("framed_opening_min_side_m", 0.3), 5.0, (H, W)))
                ref_px, what = roi_px, "growing on the structure"
            elif rule == "zone_woody":
                vm = scene["woody"] & zone
                ref_px, what = max(int(zone.sum()), 1), self.asset.get("veg_zone_label", "tree/shrub canopy (woody) in corridor")
            elif rule == "prism_all_berm_woody":
                prism = R["extra"].get("prism", roi)
                vm = (veg & prism) | (scene["woody"] & zone)
                ref_px, what = max(int(zone.sum()), 1), "weeds in canal prism / woody growth on banks"
            else:   # zone_all - e.g. PV array zone (inter-row & perimeter), pavement + shoulder, pipe RoW
                vm = veg & zone              # incl. under-table / shadow strips and growth overlapping modules (shading)
                ref_px, what = max(int(zone.sum()), 1), self.asset.get("veg_zone_label", "within the asset zone")
            min_plant = int(th["vegetation"]["min_plant_m2"] * (1000.0 / gsd) ** 2) if gsd else int(0.00005 * H * W)
            vm = mask_components(vm, max(12, min_plant))
            pct = 100.0 * vm.sum() / ref_px
            bkey = {"on_structure": "on_structure_bands_pct"}.get(rule, "zone_bands_pct")   # any growth in the asset zone = Low
            sev = band(pct, th["vegetation"][bkey]) if vm.any() else None
            if sev:
                overlay[vm > 0] = (0, 200, 0)
                self.add(amap.get("vegetation", "vegetation_structure"), sev, f"Vegetation - {what}: {pct:.1f}% of assessed zone", src, md,
                         {"vegetation_pct_of_zone": round(pct, 2), "rule": rule}, ann, bbox_of(vm), "High", {"area_m2": area_m2(vm.sum())})
        # --- moisture / wet patches
        if "moisture" in det:
            # shadow casters: trees/shrubs (woody or compact vegetation clumps) and vehicles; extended turf/crops cast none
            lo_c, hi_c = ((0.3 * (1000.0 / gsd) ** 2, 400 * (1000.0 / gsd) ** 2) if gsd else (0.00005 * H * W, 0.05 * H * W))

            def clump(st, i, lab):
                if not (lo_c <= st[4] <= hi_c):
                    return False
                _, rect_ = rectangularity(lab[st[1]:st[1] + st[3], st[0]:st[0] + st[2]] == i)
                return rect_ is not None and max(rect_[1]) <= 3 * max(1.0, min(rect_[1]))
            casters = scene["woody"] | excl | keep_components(veg, clump)
            shade = dilate(casters.astype(np.uint8), buffer_px(gsd, 1.5, 1.0, (H, W)))
            mo = moisture_mask(bgr, th["moisture"], (veg | excl | shade).astype(np.uint8), gsd) & zone
            pct = 100.0 * mo.sum() / max(int(zone.sum()), 1)
            sev = band(pct, th["moisture"]["bands_pct"]) if mo.any() else None
            if sev:
                overlay[mo > 0] = (255, 120, 0)
                self.add("moisture", sev, f"Dark wet-patch-like regions {pct:.1f}% of asset zone (verify)", src, md,
                         {"area_pct_of_zone": round(pct, 2)}, ann, bbox_of(mo), "Low", {"area_m2": area_m2(mo.sum())})
        ann = cv2.addWeighted(overlay, 0.45, ann, 0.55, 0)
        self._finish_media(rec, ann, n0)

    def _finish_media(self, rec, ann, n0):
        rec["n_findings"] = len(self.findings) - n0
        if rec["n_findings"]:
            sev = max((f.severity for f in self.findings[n0:]), key=lambda s: SEV_RANK[s])
            rec["worst"] = sev
            cv2.putText(ann, f"{rec['source'][-60:]}  [{sev}]", (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)
            if self.cfg["processing"]["save_annotated"]:
                d = self.out_dir / "annotated"
                d.mkdir(parents=True, exist_ok=True)
                name = re.sub(r"[^\w.-]+", "_", rec["source"]) + ".jpg"
                imwrite_any(d / name, ann)
                rec["annotated"] = f"annotated/{name}"
                for f in self.findings[n0:]:
                    f.metrics.setdefault("annotated", rec["annotated"])
        self.media.append(rec)

    # ---------------------------------------------------------------- thermal
    def analyze_thermal_item(self, src, md, path=None, frame=None):
        cfg = self.cfg
        mode = self.asset.get("thermal_mode", "civil_warm")
        T, how, radiometric = load_thermal(path, cfg, frame)
        H, W = T.shape
        gsd, basis = compute_gsd_mm(md, W, cfg, thermal=True)
        given, gmethod = self.thermal_given_roi(path, md, (W, H), mode)
        anomalies, stats = analyze_thermal(T, mode, cfg, gsd, given)
        roi_mask = stats.pop("roi_mask", None)
        if gmethod:
            stats["roi_method"] = gmethod
        rec = {"source": src, "kind": "thermal", "w": W, "h": H, "gsd_mm": gsd, "gsd_basis": basis,
               "lat": md.get("lat"), "lon": md.get("lon"), "rel_alt": md.get("rel_alt"), "model": md.get("model"),
               "datetime": md.get("datetime"), "flags": [] if radiometric else ["non-radiometric (palette)"],
               "thermal_source": how, "analysed": not stats.get("no_asset"),
               **{k: (round(v, 2) if isinstance(v, float) else v) for k, v in stats.items()}}
        if stats.get("no_asset"):
            rec["flags"].append("PV array not identified in thermal frame - " +
                                ("not analysed (supply a manual mask to include)" if cfg["roi"]["strict"] else "full frame analysed (Low confidence)"))
        if not (-40 < stats["Tmed"] < 200):
            rec["flags"].append("implausible temperatures - check TIFF scale/offset")
        self.cur_geo = (W, H, gsd, md)
        self.cap_conf = "Low" if stats.get("no_asset") else None
        ann = colorize_T(T)
        if roi_mask is not None:
            cnts, _ = cv2.findContours(roi_mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(ann, cnts, -1, (255, 255, 0), 1)
        n0 = len(self.findings)
        key = THERMAL_MODES[mode][0]
        pv = cfg["pv"]
        for a in anomalies:
            x, y, w, h = a["bbox"]
            col = {"Low": (0, 255, 0), "Medium": (0, 255, 255), "High": (0, 128, 255), "Critical": (0, 0, 255)}[a["severity"]]
            cv2.rectangle(ann, (x - 2, y - 2), (x + w + 2, y + h + 2), col, 2)
            cv2.putText(ann, f"{a['dT']:+.1f}C", (x, max(12, y - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, col, 1, cv2.LINE_AA)
            m = {"dT_C": round(a["dT"], 2), "T_peak_C": round(a["Tpeak"], 2), "area_px": a["area_px"],
                 "pattern": a.get("pattern"), "radiometric": radiometric}
            if "ref" in a:
                m["T_ref_C"] = round(a["ref"], 2)
            qty = {}
            if mode == "electrical":
                m["dT_c2c_load_corrected_C"] = round(a["dT_corr"], 2)
                if a.get("dT_amb") is not None:
                    m["dT_over_ambient_C"] = round(a["dT_amb"], 2)
                if a["load_corr"] != 1:
                    m["load_correction_factor"] = round(a["load_corr"], 2)
                summ = f"Hot-spot {a['Tpeak']:.1f} C, dT(c2c) {a['dT']:+.1f} C" + (f" (load-corr {a['dT_corr']:+.1f} C)" if a["load_corr"] != 1 else "")
            elif mode == "pv":
                pat = a["pattern"]
                frac = pv["loss_fraction"].get(pat)
                if frac is not None:
                    mods = pv["modules_per_string"] if pat == "string" else a.get("modules_affected", 1)
                    kw = pv["module_wp"] * frac * mods / 1000.0
                    m.update(est_power_loss_kW=round(kw, 3),
                             est_annual_loss_kWh=round(kw * pv["specific_yield_kwh_per_kwp_day"] * 365, 0),
                             est_annual_loss_value=round(kw * pv["specific_yield_kwh_per_kwp_day"] * 365 * pv["tariff_per_kwh"], 0))
                    qty = {"modules": mods}
                coa = "CoA 3 (safety-relevant)" if a["severity"] == "Critical" else ("CoA 2 (thermal abnormality)" if SEV_RANK[a["severity"]] >= 1 else "CoA 1-2 (observe)")
                m["iec62446_3_class"] = coa
                summ = f"PV {pat} anomaly, dT {a['dT']:+.1f} K, {coa}"
            else:
                m["area_pct_of_frame"] = round(a.get("area_pct", 0), 2)
                if a.get("area_m2"):
                    qty = {"area_m2": a["area_m2"]}
                summ = f"{a['pattern'].capitalize()} anomaly dT {a['dT']:+.1f} C over local background, {a.get('area_pct', 0):.1f}% of frame"
            self.add(key, a["severity"], summ, src, md, m, ann, a["bbox"], "Medium" if radiometric else "Low", qty)
        self._finish_media(rec, ann, n0)

    def thermal_given_roi(self, path, md, size, mode):
        """Manual mask, else ROI of the paired DJI visual frame (_W/_V) mapped by the thermal/visual FOV ratio."""
        if not path:
            return None, None
        W, H = size
        rc = self.cfg["roi"]
        mp = find_manual_mask(path, self.in_dir, rc)
        if mp:
            mk = imread_any(mp, cv2.IMREAD_GRAYSCALE)
            return (cv2.resize(mk, (W, H), interpolation=cv2.INTER_NEAREST) > 127).astype(np.uint8), f"manual mask ({mp.name})"
        preset = pick_preset(md, self.cfg, W, thermal=True)
        ratio = (preset or {}).get("visual_fov_ratio")
        if not ratio:
            return None, None
        p = Path(path)
        m = re.search(r"_T(?=\.[^.]+$)", p.name, re.I)
        if not m:
            return None, None
        for suf in rc["pair_visual_suffixes"]:
            vis = p.with_name(p.name[:m.start()] + suf + p.name[m.end():])
            hit = self.roi_cache.get(str(vis))
            if hit:
                roi, zone, (Wv, Hv) = hit
                src = roi if mode in ("pv", "electrical") else zone
                cw, ch = int(Wv * ratio), int(Hv * ratio)
                x0, y0 = (Wv - cw) // 2, (Hv - ch) // 2
                crop = src[y0:y0 + ch, x0:x0 + cw]
                t = cv2.resize(crop, (W, H), interpolation=cv2.INTER_NEAREST)
                return dilate(t, max(2, int(0.02 * min(W, H)))), f"paired visual {vis.name} (FOV ratio {ratio})"
        return None, None

    # ---------------------------------------------------------------- video
    def iter_video(self, path):
        p = self.cfg["processing"]
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            self.log(f"  ! cannot open video {path.name}")
            return
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        step = max(1, int(round(fps * p["video_frame_interval_s"])))
        srt = None
        for ext in (".SRT", ".srt"):
            if path.with_suffix(ext).exists():
                srt = parse_dji_srt(path.with_suffix(ext))
                break
        idx, kept, prev = 0, 0, None
        while kept < p["max_frames_per_video"] and not self.stop.is_set():
            if not cap.grab():
                break
            if idx % step == 0:
                ok, fr = cap.retrieve()
                if ok:
                    hsv = cv2.cvtColor(cv2.resize(fr, (160, 90)), cv2.COLOR_BGR2HSV)
                    hist = cv2.calcHist([hsv], [0, 1], None, [30, 32], [0, 180, 0, 256])
                    cv2.normalize(hist, hist)
                    if prev is None or cv2.compareHist(prev, hist, cv2.HISTCMP_CORREL) < p["video_dedupe_corr"]:
                        prev = hist
                        kept += 1
                        t = idx / fps
                        md = {}
                        if srt:
                            for t0, t1, la, lo, al in srt:
                                if t0 <= t <= t1 + 0.5:
                                    md = {"lat": la, "lon": lo, "rel_alt": al}
                                    break
                        yield t, fr, md
            idx += 1
        cap.release()

    # ---------------------------------------------------------------- run
    def run(self):
        self.out_dir.mkdir(parents=True, exist_ok=True)
        rgb, thermal, video = self.scan()
        self.log(f"Asset: {self.asset['label']}\nFound {len(rgb)} RGB, {len(thermal)} thermal, {len(video)} video file(s)")
        if not (rgb or thermal or video):
            raise RuntimeError("No supported media found in the selected folder.")
        total = len(rgb) + len(thermal) + len(video)
        done = 0
        tok = [t.upper() for t in self.cfg["processing"]["thermal_name_tokens"]]
        for f in rgb + thermal + video:
            if self.stop.is_set():
                self.log("Stopped by user.")
                break
            src = self.rel(f)
            self.progress(done / total, f"Analysing {src}")
            try:
                if f in video:
                    is_th = any(t in str(f).upper() for t in tok)
                    nfr = 0
                    for t, fr, md in self.iter_video(f):
                        s = f"{src}@{t:.1f}s"
                        h, w = fr.shape[:2]
                        if is_th or [w, h] in self.cfg["processing"]["thermal_resolutions"]:
                            self.analyze_thermal_item(s, md, frame=fr)
                        else:
                            self.analyze_rgb(fr, s, md, w)
                        nfr += 1
                    self.log(f"  video {src}: {nfr} unique frames analysed")
                elif f in thermal:
                    md = read_metadata(f) if f.suffix.lower() not in (".csv", ".txt") else {}
                    self.analyze_thermal_item(src, md, path=f)
                else:
                    img = imread_any(f)
                    if img is None:
                        self.log(f"  ! unreadable {src}")
                    else:
                        self.analyze_rgb(img, src, read_metadata(f), img.shape[1], path=f)
            except Exception as e:
                self.log(f"  ! error on {src}: {e}")
                traceback.print_exc()
            done += 1
        self.progress(0.97, "Writing report ...")
        paths = ReportBuilder(self).write_all()
        self.progress(1.0, "Done")
        return paths


# =============================================================================
# 9. REPORTING
# =============================================================================
def _video_time(src):
    m = re.match(r"(.+)@([\d.]+)s$", src)
    return (m.group(1), float(m.group(2))) if m else (None, None)


def group_findings(findings, cfg):
    """Merge sightings of the same defect type at the same place (overlapping frames / consecutive
    video frames) into location groups so counts and quantities are not multiplied by image overlap."""
    rp, rcam = cfg["report"]["dedupe_radius_projected_m"], cfg["report"]["dedupe_radius_camera_m"]
    gap = 2.5 * cfg["processing"]["video_frame_interval_s"]
    groups = []
    for f in findings:
        vid, t = _video_time(f.source)
        hit = None
        for g in groups:
            r = g[0]
            if r.defect != f.defect:
                continue
            if f.lat is not None and r.lat is not None:
                both_proj = f.metrics.get("location_basis") == "projected" and r.metrics.get("location_basis") == "projected"
                dn = (f.lat - r.lat) * 111320.0
                de = (f.lon - r.lon) * 111320.0 * math.cos(math.radians(f.lat))
                if math.hypot(dn, de) <= (rp if both_proj else rcam):
                    hit = g
                    break
            elif vid is not None:
                rv, rt = _video_time(g[-1].source)
                if rv == vid and abs(t - rt) <= gap:
                    hit = g
                    break
        if hit is not None:
            hit.append(f)
        else:
            groups.append([f])
    for k, g in enumerate(groups, 1):
        for f in g:
            f.group = f"L{k:03d}"
    return groups


def health_index(findings, n_media, cfg, groups=None):
    w = cfg["severity_weights"]
    units = [max(g, key=lambda f: SEV_RANK[f.severity]) for g in groups] if groups else findings
    pen = sum(w[f.severity] for f in units) / max(1, n_media)
    hi = 100.0 * math.exp(-pen / 10.0)   # pen = mean severity weight per media item
    sevs = {f.severity for f in findings}
    if "Critical" in sevs:
        hi = min(hi, 39.0)
    elif "High" in sevs:
        hi = min(hi, 69.0)
    if hi >= 85:
        rating = "GOOD - routine maintenance (indicative NBI 7-9)"
    elif hi >= 70:
        rating = "FAIR - minor deterioration, planned repairs (indicative NBI 5-6)"
    elif hi >= 40:
        rating = "POOR - significant deterioration, repairs required (indicative NBI 4)"
    else:
        rating = "SERIOUS / CRITICAL - urgent intervention (indicative NBI <= 3)"
    return round(hi, 1), rating


class ReportBuilder:
    def __init__(self, eng):
        self.e, self.cfg = eng, eng.cfg
        self.groups = group_findings(eng.findings, eng.cfg)
        self.f = sorted(eng.findings, key=lambda x: (-SEV_RANK[x.severity], x.defect, x.source))
        self.n_analysed = sum(1 for m in eng.media if m.get("analysed", True))
        self.hi, self.rating = health_index(eng.findings, self.n_analysed, eng.cfg, self.groups)
        self.by_defect = {}
        for x in self.f:
            self.by_defect.setdefault(x.defect, []).append(x)

    def counts(self):
        return {s: sum(1 for x in self.f if x.severity == s) for s in SEVERITIES}

    def quantities(self):
        """Per defect type: max within each location group (overlapping views of the same defect),
        summed across groups."""
        q = {}
        for g in self.groups:
            k = g[0].defect
            agg = q.setdefault(k, {})
            best = {}
            for x in g:
                for qk, qv in x.quantity.items():
                    if qv is not None:
                        best[qk] = max(best.get(qk, 0), qv)
            for qk, qv in best.items():
                agg[qk] = agg.get(qk, 0) + qv
        return q

    def n_locations(self, defect=None, severity=None):
        return sum(1 for g in self.groups if (defect is None or g[0].defect == defect) and
                   (severity is None or max(SEV_RANK[f.severity] for f in g) == SEV_RANK[severity]))

    def acquisition_qa(self):
        env, msgs = self.cfg["environment"], []
        skipped = [m for m in self.e.media if m.get("analysed") is False]
        meth = {}
        for m in self.e.media:
            if m.get("roi_method"):
                meth[m["roi_method"]] = meth.get(m["roi_method"], 0) + 1
        msgs.append(f"Asset zone (ROI): analysed {self.n_analysed} of {len(self.e.media)} frames; "
                    + "; ".join(f"{v} x {k}" for k, v in meth.items()))
        if skipped:
            msgs.append(f"WARNING: {len(skipped)} frame(s) not analysed because the asset could not be identified "
                        f"(e.g. {', '.join(m['source'] for m in skipped[:5])}). Add a manual mask (<image>_mask.png, white = asset) "
                        "to include them, or check that the correct work type was selected.")
        mode = self.e.asset.get("thermal_mode")
        th = [m for m in self.e.media if m["kind"] == "thermal"]
        rg = [m for m in self.e.media if m["kind"] == "rgb"]
        if rg:
            bl = sum("blurred" in m["flags"] for m in rg)
            ex = sum(any("exposed" in f for f in m["flags"]) for m in rg)
            ng = sum(m["gsd_mm"] is None for m in rg)
            gs = [m["gsd_mm"] for m in rg if m["gsd_mm"]]
            msgs.append(f"RGB frames: {len(rg)}; blurred {bl}; exposure issues {ex}; no-GSD {ng}" +
                        (f"; GSD {min(gs):.2f}-{max(gs):.2f} mm/px (median {np.median(gs):.2f})" if gs else ""))
            if gs and self.e.asset_key in ("bridge", "building", "dam") and np.median(gs) > 1.0:
                msgs.append("WARNING: median GSD > 1 mm/px - cracks < ~1 mm cannot be reliably resolved; fly closer / use zoom for crack mapping (target <= 0.3 mm/px for 0.2-0.3 mm cracks).")
            if gs and self.e.asset_key in ("road", "canal") and np.median(gs) > 3.0:
                msgs.append(f"WARNING: median GSD {np.median(gs):.1f} mm/px - cracks narrower than ~1 pixel ({np.median(gs):.0f} mm) are not reliably detected; "
                            "pavement / lining crack surveys need <= 2-3 mm/px (fly lower or use a zoom payload). Potholes, panel damage and vegetation are still assessed.")
            if not gs:
                msgs.append("WARNING: GSD unknown - crack widths/areas not quantified. Set camera preset, object distance or GSD override in Settings.")
        if th:
            nr = sum("non-radiometric (palette)" in m["flags"] for m in th)
            msgs.append(f"Thermal frames: {len(th)}; non-radiometric {nr}" + (" -> temperatures are indicative only (palette inversion)." if nr else ""))
            if mode == "pv":
                irr = env.get("irradiance_wm2")
                if irr is None:
                    msgs.append("Irradiance not recorded - IEC TS 62446-3 requires >= 600 W/m2 (stable) for valid PV thermography.")
                elif irr < self.cfg["pv"]["min_irradiance_wm2"]:
                    msgs.append(f"NON-COMPLIANT: irradiance {irr} W/m2 < {self.cfg['pv']['min_irradiance_wm2']} W/m2 - anomalies may be under-stated; repeat scan.")
            if mode == "electrical":
                if env.get("load_pct") is None:
                    msgs.append("Load at time of scan not recorded - dT cannot be load-normalised; scan at >= 40 % of rated load recommended.")
                elif env["load_pct"] < 40:
                    msgs.append(f"Load {env['load_pct']} % < 40 % - load correction applied (dT x (100/load)^{env.get('load_exponent', 2)}); treat with caution.")
                if env.get("ambient_temp_c") is None:
                    msgs.append("Ambient temperature not recorded - over-ambient NETA criteria not evaluated.")
            if env.get("wind_speed_ms") and env["wind_speed_ms"] > 7:
                msgs.append(f"Wind {env['wind_speed_ms']} m/s - convective cooling reduces apparent dT (>~7 m/s generally unsuitable).")
        return msgs

    # ------------------------------------------------------------------ HTML
    def html(self):
        e, cfg, P = self.e, self.cfg, self.e.project
        esc = lambda s: html.escape(str(s if s is not None else ""))
        c = self.counts()
        qty = self.quantities()
        css = """
        body{font-family:Segoe UI,Roboto,Arial,sans-serif;margin:0;background:#f4f6f8;color:#1c2833}
        .wrap{max-width:1180px;margin:auto;background:#fff;padding:28px 36px}
        h1{margin:0;font-size:26px}h2{border-bottom:3px solid #1f4e79;padding-bottom:4px;margin-top:34px;color:#1f4e79}
        h3{color:#1f4e79;margin-bottom:4px}table{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0}
        th,td{border:1px solid #cfd8dc;padding:5px 7px;vertical-align:top;text-align:left}th{background:#eaf1f8}
        .sev{color:#fff;padding:2px 8px;border-radius:10px;font-weight:600;font-size:12px;white-space:nowrap}
        .kpi{display:inline-block;min-width:150px;margin:6px 10px 6px 0;padding:10px 14px;border-radius:8px;background:#eef3f8}
        .kpi b{font-size:22px;display:block}.cover{background:#1f4e79;color:#fff;padding:22px 26px;border-radius:8px}
        .cover table{width:auto}.cover td{border:none;color:#fff;padding:2px 18px 2px 0}.thumb{max-width:260px;border:1px solid #ccc}
        .warn{background:#fff3e0;border-left:4px solid #e65100;padding:6px 10px;margin:4px 0}
        ul{margin:4px 0 8px 18px;padding:0}li{margin:2px 0}.small{font-size:12px;color:#555}
        .card{border:1px solid #d6dee6;border-radius:8px;padding:10px 16px;margin:14px 0;page-break-inside:avoid}
        @media print{body{background:#fff}.wrap{padding:0}h2{page-break-before:auto}}
        """
        sev = lambda s: f'<span class="sev" style="background:{SEV_COLOR[s]}">{s}</span>'
        L = [f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>Inspection Report - {esc(P.get('project'))}</title><style>{css}</style></head><body><div class='wrap'>"]
        logo = cfg["report"].get("logo_path")
        if logo and Path(logo).exists():
            img = imread_any(logo)
            if img is not None:
                L.append(f"<img src='data:image/jpeg;base64,{b64_jpg(img, 220)}' style='float:right;max-height:70px'>")
        L.append(f"<div class='cover'><h1>UAV INSPECTION REPORT - {esc(e.asset['label']).upper()}</h1><table>")
        for k, v in [("Project", P.get("project")), ("Structure / Asset ID", P.get("asset_id")), ("Location", P.get("location")),
                     ("Client", P.get("client")), ("Inspection agency", cfg["report"].get("company")), ("Inspector / Pilot", P.get("inspector")),
                     ("Survey date", P.get("date")), ("Report generated", dt.datetime.now().strftime("%Y-%m-%d %H:%M")),
                     ("Software", f"{APP_NAME} v{VERSION}")]:
            L.append(f"<tr><td><b>{k}</b></td><td>{esc(v)}</td></tr>")
        L.append("</table></div>")
        # 1 Executive summary
        L.append("<h2>1. Executive Summary</h2>")
        L.append(f"<div class='kpi'>Health index<b>{self.hi}/100</b></div><div class='kpi'>Frames analysed<b>{self.n_analysed}/{len(e.media)}</b></div>"
                 f"<div class='kpi'>Defect locations<b>{len(self.groups)}</b></div><div class='kpi'>Detections<b>{len(self.f)}</b></div>" +
                 "".join(f"<div class='kpi' style='border-left:6px solid {SEV_COLOR[s]}'>{s}<b>{c[s]}</b></div>" for s in reversed(SEVERITIES)))
        L.append(f"<p><b>Overall condition rating:</b> {esc(self.rating)}</p>")
        L.append(self.svg_bar(c))
        if self.f:
            L.append("<p><b>Principal findings (highest severity per defect type):</b></p><ul>")
            for k, lst in sorted(self.by_defect.items(), key=lambda kv: -SEV_RANK[kv[1][0].severity]):
                L.append(f"<li>{sev(lst[0].severity)} <b>{esc(kb_entry(cfg, k)['name'])}</b> - {self.n_locations(k)} location(s) ({len(lst)} detection(s)); worst: {esc(lst[0].summary)} [{esc(lst[0].source)}]</li>")
            L.append("</ul>")
            crit = [x for x in self.f if x.severity == "Critical"]
            if crit:
                L.append(f"<div class='warn'><b>IMMEDIATE ACTION:</b> {len(crit)} critical finding(s). {esc(cfg['priority']['Critical'][1])}.</div>")
        else:
            L.append("<p>No defects above reporting thresholds were detected in the analysed media. Verify coverage and image quality before concluding.</p>")
        # 2 Scope & data QA
        L.append("<h2>2. Scope, Data &amp; Acquisition Quality</h2><ul>")
        L.append(f"<li><b>Asset type:</b> {esc(e.asset['label'])}; <b>components in scope:</b> {esc(', '.join(e.asset.get('components', [])))}</li>")
        L.append(f"<li><b>Input folder:</b> {esc(e.in_dir)}</li>")
        env = cfg["environment"]
        cond = ", ".join(f"{k.replace('_', ' ')} = {v}" for k, v in env.items() if v is not None and k != "load_exponent")
        L.append(f"<li><b>Conditions at survey:</b> {esc(cond or 'not recorded')}</li>")
        L.append("</ul>")
        for m in self.acquisition_qa():
            L.append(f"<div class='warn'>{esc(m)}</div>" if any(t in m for t in ("WARNING", "NON-COMPLIANT", "not recorded", "indicative", "<", "caution")) else f"<p>{esc(m)}</p>")
        # 3 Methodology
        L.append("<h2>3. Methodology &amp; Assessment Criteria</h2><ul>")
        th = cfg["thresholds"]
        rule_txt = {"on_structure": "vegetation counted only where it grows ON the asset (>= 80 % of its boundary is asset surface, not running out of frame)",
                    "zone_woody": "only woody vegetation (trees/shrubs: textured canopy >= 2 m2) counted; crops, grass and turf ignored",
                    "zone_all": "vegetation counted only inside the asset zone (e.g. PV array inter-row/perimeter, pipe RoW buffer)",
                    "prism_all_berm_woody": "all vegetation inside the canal prism; on berms/banks only woody growth"}
        meth = [
            f"Asset zone first: each frame is segmented to the asset ({e.asset.get('roi', 'full')}) and EVERY detector runs only on the asset "
            "surface or its defined buffer - adjacent land, empty plots, fields, vehicles, roofs, soil, sky and water are excluded. "
            f"Vegetation rule: {rule_txt.get(e.asset.get('veg_rule'), e.asset.get('veg_rule'))}. "
            "Percentages are of the asset surface/zone in frame, not the whole photo. Frames where the asset is not identified are not analysed "
            "(strict mode); a manual mask (<image>_mask.png or masks/<image>.png, white = asset) overrides the automatic zone. "
            "PV modules are verified by rectangularity and a periodic cell/frame grid at module pitch (vehicles, corrugated sheds, parking rows and water fail this test). "
            "Vehicles (any colour) and their shadows are excluded where vehicles use the asset (roads, decks, service roads, crests, PV tracks). "
            "Long straight lines (joints, member edges, lane markings, formwork and shadow lines) are removed before crack measurement.",
            "Duplicate control: the same defect type seen in overlapping frames within "
            f"{self.cfg['report']['dedupe_radius_projected_m']} m (ground-projected) / {self.cfg['report']['dedupe_radius_camera_m']} m (camera position) "
            "or in consecutive video frames is one location; quantities take the maximum per location, summed across locations.",
            "Image QA: variance-of-Laplacian sharpness (normalised 1000 px), mean-luminance exposure check.",
            "GSD = sensor width x distance / (focal length x image width); distance = user object distance, LRF, or relative altitude (slant-corrected by gimbal pitch).",
            f"Cracks: CLAHE + morphological black-hat (k={th['crack']['blackhat_kernel_px']} px), Otsu threshold, elongation & length filters, thinning skeleton; width = 2·d-1 from Euclidean distance transform (95th percentile). Bands (mm): {th['crack']['width_bands_mm']}; pavements {th['crack']['pavement_width_bands_mm']}.",
            f"Corrosion: HSV segmentation {th['corrosion']['hsv_lo']}-{th['corrosion']['hsv_hi']}, graded to ISO 4628-3 Ri (limits {th['corrosion']['ri_limits_pct']} % area).",
            f"Vegetation: Excess-Green index (2g-r-b)/(r+g+b) > {th['vegetation']['exg_min']}, counted per the asset rule above; any growth in the asset zone is at least Low "
            f"({th['vegetation']['zone_bands_pct']} % of zone; on structures {th['vegetation']['on_structure_bands_pct']} % of surface). Woody = textured canopy >= {cfg['roi']['woody_min_area_m2']} m2.",
            f"Spall/void/pothole: dark region vs robust local background, darker than its own surroundings on all sides (ring 30th percentile >= {th['spall']['ring_drop']} grey levels), "
            f">= {th['spall']['min_area_m2']} m2, not rectangular (drains, hatches, plates), not thin (shadow bars, strokes), not touching vehicles; escalated when adjacent to rust; spall perimeter is not double-counted as cracks.",
            "Efflorescence: low-saturation / high-value deposits, irregular outline (white panels, AC units, markings rejected), sky suppressed. "
            "Moisture/wet patch: irregular, non-elongated area clearly darker than its own surroundings, excluding cast shadows of trees, shrubs and vehicles (low confidence - corroborate with thermal).",
            "Thermal: radiometric data used where available (TIFF/CSV/dji_irp); palette images inverted via LUT using operator-entered scale (indicative).",
            {"electrical": f"Electrical thermal criteria NETA MTS Table 100.18 - component-to-component {th['thermal_electrical_c2c']}; over-ambient {th['thermal_electrical_amb']} (C), optional load correction.",
             "pv": f"PV thermal: dT vs robust array reference, bands {th['thermal_pv']} K; pattern by anomaly area / module area ({cfg['pv']['module_length_m']} x {cfg['pv']['module_width_m']} m); IEC TS 62446-3 CoA classes.",
             }.get(e.asset.get("thermal_mode"), f"Thermal ({e.asset.get('thermal_mode')}): local anomaly vs Gaussian background (sigma {cfg['thermal']['local_bg_fraction']} x frame), bands {th.get(THERMAL_MODES.get(e.asset.get('thermal_mode'), ('', 'thermal_civil_warm'))[1], '')}."),
            f"Health index: 100 x exp(-P/10), P = (sum of severity weights {cfg['severity_weights']} over defect locations / frames analysed); capped at 39 if any Critical and 69 if any High. Rating bands are indicative.",
        ]
        L += [f"<li>{esc(m)}</li>" for m in meth] + ["</ul>"]
        # 4 Defect-wise analysis
        L.append("<h2>4. Defect-wise Analysis, Solutions &amp; Improvement Measures</h2>")
        for k, lst in sorted(self.by_defect.items(), key=lambda kv: -SEV_RANK[kv[1][0].severity]):
            kb = kb_entry(cfg, k)
            pr = cfg["priority"][lst[0].severity]
            L.append(f"<div class='card'><h3>{esc(kb['name'])} &nbsp; {sev(lst[0].severity)} &nbsp; <span class='small'>{self.n_locations(k)} location(s), {len(lst)} detection(s) - Priority {pr[0]}</span></h3>")
            L.append(f"<p class='small'>{esc(kb['description'])}</p>")
            dist = ", ".join(f"{s}: {sum(1 for x in lst if x.severity == s)}" for s in reversed(SEVERITIES) if any(x.severity == s for x in lst))
            L.append(f"<p><b>Severity distribution:</b> {dist}. ")
            if qty.get(k):
                L.append("<b>Indicative quantity:</b> " + "; ".join(f"{qk.replace('_', ' ')} = {qv:,.2f}" for qk, qv in qty[k].items()))
            L.append("</p>")
            pvsum = sum(x.metrics.get("est_annual_loss_kWh", 0) for x in lst)
            if pvsum:
                val = sum(x.metrics.get("est_annual_loss_value", 0) for x in lst)
                kw = sum(x.metrics.get("est_power_loss_kW", 0) for x in lst)
                L.append(f"<p><b>Estimated generation loss:</b> {kw:.2f} kWp equivalent, ~{pvsum:,.0f} kWh/yr (~{cfg['pv']['currency']} {val:,.0f}/yr) - indicative.</p>")
            hints = sorted({x.metrics.get("diagnostic_hint") for x in lst if x.metrics.get("diagnostic_hint")})
            if hints:
                L.append("<p><b>Diagnostic hints:</b> " + esc("; ".join(hints)) + "</p>")
            for title, key in [("Probable causes", "causes"), ("Consequences if unattended", "consequences"), ("Recommended solution / repair method", "solution"),
                               ("Improvement & preventive measures", "improvement"), ("Necessary actions / further investigation", "actions"),
                               ("Recommended NDT", "ndt"), ("Reference standards", "references")]:
                if kb.get(key):
                    L.append(f"<b>{title}</b><ul>" + "".join(f"<li>{esc(t)}</li>" for t in kb[key]) + "</ul>")
            L.append("</div>")
        # 5 Action plan
        L.append("<h2>5. Prioritised Action Plan</h2><table><tr><th>#</th><th>Priority</th><th>Defect</th><th>Timeframe</th><th>Action</th><th>Locations (worst first)</th></tr>")
        i = 0
        for k, lst in sorted(self.by_defect.items(), key=lambda kv: -SEV_RANK[kv[1][0].severity]):
            for s in reversed(SEVERITIES):
                sub = [x for x in lst if x.severity == s]
                if not sub:
                    continue
                i += 1
                kb = kb_entry(cfg, k)
                pr = cfg["priority"][s]
                act = (kb["solution"][:2] if SEV_RANK[s] >= 2 else kb["actions"][:1] + kb["solution"][:1])
                seen, uniq = set(), []
                for x in sub:                     # one entry per location group
                    if x.group not in seen:
                        seen.add(x.group)
                        uniq.append(x)
                locs = ", ".join(f"{x.group}: {x.fid} ({x.source})" for x in uniq[:8]) + (f" ... +{len(uniq) - 8}" if len(uniq) > 8 else "")
                L.append(f"<tr><td>{i}</td><td>{sev(s)} {pr[0]}</td><td>{esc(kb['name'])}</td><td>{esc(pr[1])}</td><td>{esc('; '.join(act))}</td><td class='small'>{esc(locs)}</td></tr>")
        L.append("</table>")
        # 6 Findings register
        L.append("<h2>6. Findings Register</h2><table><tr><th>ID</th><th>Loc.</th><th>Severity</th><th>Defect</th><th>Source</th><th>GPS</th><th>Details</th><th>Image</th></tr>")
        for x in self.f[:cfg["report"]["max_register_rows"]]:
            gps = f"{x.lat:.6f}, {x.lon:.6f}" if x.lat is not None and x.lon is not None else "-"
            met = "<br>".join(f"{esc(k)}: {esc(v)}" for k, v in x.metrics.items() if k != "annotated")
            img = f"<img class='thumb' src='data:image/jpeg;base64,{x.thumb}'>" if x.thumb else ""
            L.append(f"<tr><td>{x.fid}</td><td>{x.group}</td><td>{sev(x.severity)}<br><span class='small'>conf. {x.confidence}</span></td><td>{esc(kb_entry(cfg, x.defect)['name'])}</td>"
                     f"<td class='small'>{esc(x.source)}</td><td class='small'>{gps}</td><td class='small'><b>{esc(x.summary)}</b><br>{met}</td><td>{img}</td></tr>")
        L.append("</table>")
        if len(self.f) > cfg["report"]["max_register_rows"]:
            L.append(f"<p class='small'>{len(self.f) - cfg['report']['max_register_rows']} further findings in findings.csv.</p>")
        # 7 Map
        pts = [(x.lat, x.lon, x.severity) for x in self.f if x.lat is not None and x.lon is not None]
        if pts:
            L.append("<h2>7. Defect Location Plot (WGS-84)</h2>" + self.svg_map(pts, [(m["lat"], m["lon"]) for m in e.media if m.get("lat") is not None]))
        # 8 Manual checklist & maintenance
        L.append("<h2>8. Items Requiring Ground / Manual Verification</h2><p class='small'>Not assessable (or not reliably assessable) from the supplied imagery.</p><ul>")
        L += [f"<li>&#9744; {esc(t)}</li>" for t in e.asset.get("manual_checklist", [])] + ["</ul>"]
        L.append("<h2>9. Maintenance &amp; Monitoring Programme</h2><ul>")
        L.append(f"<li><b>Inspection frequency:</b> {esc(e.asset.get('frequency', '-'))}</li>")
        L.append("<li>Re-fly the same mission plan (identical altitude/overlap/GSD) for change detection; compare crack lengths/widths, rust Ri grade and thermal dT trends.</li>")
        L.append("<li>Re-inspect all P1/P2 locations after repair to confirm effectiveness (thermal re-scan for electrical/PV within 1 month).</li>")
        L.append(f"<li><b>Governing references:</b> {esc('; '.join(e.asset.get('references', [])))}</li></ul>")
        # 10 Limitations
        L.append("<h2>10. Limitations &amp; Disclaimer</h2><ul class='small'>")
        for t in ["Automated detections are screening-level candidates generated by classical computer-vision and radiometric algorithms; false positives (shadows, joints, stains, formwork marks, reflections) and false negatives (sub-GSD cracks, hidden faces) are expected. All Critical/High findings must be verified in the field by a competent engineer.",
                  "Crack widths below ~1-2 px of GSD are not measurable; widths are surface widths at time of survey.",
                  "Analysis is restricted to the automatically delineated asset zone (or the supplied manual mask). Unusual scenes (e.g. painted structures, coloured pavements, bifacial/frameless modules, dense urban clutter) can be under- or over-delineated - review the cyan (asset) and yellow (buffer zone) outlines on the annotated images and supply a manual mask where needed.",
                  "Percentages are of the asset surface visible in each frame; true member-level extent requires orthomosaic / 3D model mapping. Thin soiling strips along module edges are suppressed together with inter-module gaps; individual bird droppings are only resolved on close-range imagery (<= 10 mm/px).",
                  "Known look-alikes that colour/shape analysis cannot separate: graffiti strokes (may read as cracks) and orange paint (may read as rust); plants standing in front of vegetation seen behind the structure; turbid grey water next to concrete; oil stains vs. shallow potholes. Review such findings on the annotated image.",
                  "Crack widths below ~1 pixel are sub-pixel estimates (flagged); the reported maximum is conservative (typically +0.1-0.3 mm at 0.4 mm/px). Width uncertainty is +/- 1 px at the stated GSD.",
                  "Thermal-only frames (no paired visual image / mask) cannot separate the structure from warm or cool background (river banks, vegetation) in oblique views; supply the DJI wide image (_W/_V) or a mask. Sun/shadow boundaries larger than 4 % of the frame are not assessed as delamination.",
                  "Thermal temperatures from non-radiometric (palette) images are indicative; emissivity, reflected temperature, distance, humidity and wind affect radiometric accuracy.",
                  "This report supports, and does not replace, statutory inspections, NDT, structural assessment/load rating, or electrical testing."]:
            L.append(f"<li>{esc(t)}</li>")
        L.append("</ul>")
        L.append("<table><tr><th>Prepared by</th><th>Reviewed by</th><th>Approved by</th></tr>"
                 f"<tr><td style='height:60px'>{esc(cfg['report']['prepared_by'] or P.get('inspector'))}</td><td>{esc(cfg['report']['reviewed_by'])}</td><td>{esc(cfg['report']['approved_by'])}</td></tr></table>")
        # Appendix
        if cfg["report"]["include_media_appendix"]:
            L.append("<h2>Appendix A - Media Index</h2><table><tr><th>Source</th><th>Type</th><th>Res.</th><th>GSD mm/px</th><th>GSD basis</th><th>GPS</th><th>QA flags</th><th>Findings</th></tr>")
            for m in e.media:
                gps = f"{m['lat']:.6f}, {m['lon']:.6f}" if m.get("lat") is not None else "-"
                g = f"{m['gsd_mm']:.2f}" if m.get("gsd_mm") else "-"
                L.append(f"<tr class='small'><td>{esc(m['source'])}</td><td>{m['kind']}</td><td>{m['w']}x{m['h']}</td><td>{g}</td><td>{esc(m.get('gsd_basis'))}</td><td>{gps}</td><td>{esc(', '.join(m['flags']))}</td><td>{m.get('n_findings', 0)}</td></tr>")
            L.append("</table>")
        L.append("</div></body></html>")
        return "\n".join(L)

    def svg_bar(self, c):
        mx = max(1, max(c.values()))
        bars = []
        for i, s in enumerate(reversed(SEVERITIES)):
            w = int(380 * c[s] / mx)
            bars.append(f"<text x='0' y='{22 + i * 26}' font-size='13'>{s}</text><rect x='70' y='{9 + i * 26}' width='{w}' height='18' fill='{SEV_COLOR[s]}'/>"
                        f"<text x='{76 + w}' y='{22 + i * 26}' font-size='13'>{c[s]}</text>")
        return f"<svg width='520' height='115'>{''.join(bars)}</svg>"

    def svg_map(self, pts, track):
        allp = pts + [(a, b, None) for a, b in track]
        las, los = [p[0] for p in allp], [p[1] for p in allp]
        la0, la1, lo0, lo1 = min(las), max(las), min(los), max(los)
        kx = math.cos(math.radians((la0 + la1) / 2))
        sx, sy = max((lo1 - lo0) * kx, 1e-6), max(la1 - la0, 1e-6)
        sc = 640 / max(sx, sy)
        X = lambda lo: 30 + (lo - lo0) * kx * sc
        Y = lambda la: 30 + (la1 - la) * sc
        span_m = max(sx, sy) * 111320
        el = [f"<circle cx='{X(b):.1f}' cy='{Y(a):.1f}' r='2' fill='#90a4ae'/>" for a, b in track]
        el += [f"<circle cx='{X(b):.1f}' cy='{Y(a):.1f}' r='6' fill='{SEV_COLOR[s]}' fill-opacity='0.8' stroke='#000' stroke-width='0.5'/>"
               for a, b, s in sorted(pts, key=lambda p: SEV_RANK[p[2]])]
        return (f"<svg width='700' height='{int(60 + sy * sc)}' style='border:1px solid #ccc;background:#fafafa'>{''.join(el)}</svg>"
                f"<p class='small'>Extent ~{span_m:,.0f} m; grey = all image centres; coloured = findings by severity. Bounds lat {la0:.6f}-{la1:.6f}, lon {lo0:.6f}-{lo1:.6f}.</p>")

    # ------------------------------------------------------------------ DOCX
    def docx(self, path):
        e, cfg, P = self.e, self.cfg, self.e.project
        d = docx.Document()
        st = d.styles["Normal"]
        st.font.name, st.font.size = "Calibri", Pt(10)
        d.add_heading(f"UAV Inspection Report - {e.asset['label']}", 0)
        t = d.add_table(rows=0, cols=2)
        t.style = "Light Grid Accent 1"
        for k, v in [("Project", P.get("project")), ("Asset ID", P.get("asset_id")), ("Location", P.get("location")), ("Client", P.get("client")),
                     ("Agency", cfg["report"].get("company")), ("Inspector", P.get("inspector")), ("Survey date", P.get("date")),
                     ("Health index", f"{self.hi}/100"), ("Condition rating", self.rating)]:
            r = t.add_row().cells
            r[0].text, r[1].text = k, str(v or "")
        d.add_heading("1. Executive summary", 1)
        c = self.counts()
        d.add_paragraph(f"{len(e.media)} media analysed; {len(self.f)} findings - " + ", ".join(f"{s}: {c[s]}" for s in reversed(SEVERITIES)))
        for k, lst in sorted(self.by_defect.items(), key=lambda kv: -SEV_RANK[kv[1][0].severity]):
            d.add_paragraph(f"[{lst[0].severity}] {kb_entry(cfg, k)['name']} - {self.n_locations(k)} location(s), {len(lst)} detection(s); worst: {lst[0].summary}", style="List Bullet")
        d.add_heading("2. Data & acquisition quality", 1)
        for m in self.acquisition_qa():
            d.add_paragraph(m, style="List Bullet")
        d.add_heading("3. Defect-wise analysis, solutions & improvement measures", 1)
        qty = self.quantities()
        for k, lst in sorted(self.by_defect.items(), key=lambda kv: -SEV_RANK[kv[1][0].severity]):
            kb = kb_entry(cfg, k)
            d.add_heading(f"{kb['name']} - {lst[0].severity} ({cfg['priority'][lst[0].severity][0]})", 2)
            d.add_paragraph(kb["description"])
            if qty.get(k):
                d.add_paragraph("Indicative quantity: " + "; ".join(f"{a.replace('_', ' ')} = {b:,.2f}" for a, b in qty[k].items()))
            for title, key in [("Probable causes", "causes"), ("Consequences", "consequences"), ("Solution / repair", "solution"),
                               ("Improvement measures", "improvement"), ("Necessary actions", "actions"), ("NDT", "ndt"), ("References", "references")]:
                if kb.get(key):
                    p = d.add_paragraph()
                    p.add_run(title).bold = True
                    for s in kb[key]:
                        d.add_paragraph(s, style="List Bullet")
        d.add_heading("4. Prioritised action plan", 1)
        t = d.add_table(rows=1, cols=4)
        t.style = "Light Grid Accent 1"
        for i, h in enumerate(["Priority", "Defect", "Timeframe", "Locations"]):
            t.rows[0].cells[i].text = h
        for k, lst in sorted(self.by_defect.items(), key=lambda kv: -SEV_RANK[kv[1][0].severity]):
            for s in reversed(SEVERITIES):
                sub = [x for x in lst if x.severity == s]
                if sub:
                    r = t.add_row().cells
                    r[0].text, r[1].text = f"{cfg['priority'][s][0]} ({s})", kb_entry(cfg, k)["name"]
                    r[2].text, r[3].text = cfg["priority"][s][1], ", ".join(x.fid for x in sub[:15])
        d.add_heading("5. Findings register (top 60 with images)", 1)
        for x in self.f[:60]:
            d.add_paragraph(f"{x.fid} [{x.severity}] {kb_entry(cfg, x.defect)['name']} - {x.source}", style="List Bullet")
            d.add_paragraph(x.summary)
            if x.thumb:
                with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
                    tf.write(base64.b64decode(x.thumb))
                d.add_picture(tf.name, width=Cm(8))
                os.unlink(tf.name)
        d.add_heading("6. Manual verification checklist", 1)
        for s in e.asset.get("manual_checklist", []):
            d.add_paragraph("[ ] " + s)
        d.add_heading("7. Limitations", 1)
        d.add_paragraph("Automated findings are screening-level candidates; verify all Critical/High items in the field. See HTML report for full methodology.")
        d.save(str(path))

    def write_all(self):
        out = self.e.out_dir
        paths = {}
        p = out / "Inspection_Report.html"
        p.write_text(self.html(), encoding="utf-8")
        paths["html"] = p
        with open(out / "findings.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["id", "location_group", "severity", "priority", "defect_key", "defect", "source", "lat", "lon", "confidence", "summary", "metrics", "quantity"])
            for x in self.f:
                w.writerow([x.fid, x.group, x.severity, self.cfg["priority"][x.severity][0], x.defect, kb_entry(self.cfg, x.defect)["name"], x.source,
                            x.lat, x.lon, x.confidence, x.summary, json.dumps(x.metrics), json.dumps(x.quantity)])
        with open(out / "media_index.csv", "w", newline="", encoding="utf-8") as fh:
            keys = ["source", "kind", "w", "h", "gsd_mm", "gsd_basis", "lat", "lon", "rel_alt", "model", "datetime", "blur", "brightness",
                    "analysed", "roi_method", "roi_cover_pct", "flags", "n_findings", "annotated"]
            w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            for m in self.e.media:
                w.writerow({**m, "flags": ";".join(m["flags"])})
        js = {"project": self.e.project, "asset": self.e.asset_key, "health_index": self.hi, "rating": self.rating,
              "frames_analysed": self.n_analysed, "frames_total": len(self.e.media), "defect_locations": len(self.groups),
              "counts": self.counts(), "quantities": self.quantities(),
              "findings": [{k: v for k, v in asdict(x).items() if k != "thumb"} for x in self.f]}
        (out / "findings.json").write_text(json.dumps(js, indent=2, default=str), encoding="utf-8")
        if self.cfg["processing"]["make_docx"] and HAVE_DOCX:
            try:
                self.docx(out / "Inspection_Report.docx")
                paths["docx"] = out / "Inspection_Report.docx"
            except Exception as ex:
                self.e.log(f"DOCX failed: {ex}")
        return paths


# =============================================================================
# 10. GUI  (Tkinter - ships with standard Python on Windows/macOS)
#     Workflow: 1) pick asset  2) pick data folder  3) (optional) details  4) RUN
# =============================================================================
def open_path(p):
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(p))  # noqa
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p)])
    except Exception as e:
        print("Cannot open:", e)


def run_gui():
    import queue
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, scrolledtext

    cfg = load_config()
    q = queue.Queue()
    root = tk.Tk()
    root.title(f"{APP_NAME} v{VERSION} - UAV Inspection Analytics")
    root.geometry("1040x760")
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure("Big.TButton", font=("Segoe UI", 13, "bold"), padding=10)
    style.configure("Asset.TRadiobutton", font=("Segoe UI", 11), padding=6)
    style.configure("H.TLabel", font=("Segoe UI", 12, "bold"))

    nb = ttk.Notebook(root)
    nb.pack(fill="both", expand=True, padx=8, pady=8)
    run_tab, set_tab, log_tab = ttk.Frame(nb), ttk.Frame(nb), ttk.Frame(nb)
    nb.add(run_tab, text="  1 · Run Inspection  ")
    nb.add(set_tab, text="  2 · Settings / Customise  ")
    nb.add(log_tab, text="  3 · Log  ")

    V = {k: tk.StringVar() for k in ["asset", "inp", "out", "project", "asset_id", "location", "client", "inspector", "date",
                                     "preset", "tpreset", "dist", "gsd", "palette", "tmin", "tmax", "amb", "irr", "wind", "load", "interval"]}
    assets = all_assets(cfg)
    V["asset"].set(next(iter(assets)))
    V["date"].set(dt.date.today().isoformat())

    def refresh_from_cfg():
        V["preset"].set(cfg["geometry"]["camera_preset"])
        V["tpreset"].set(cfg["geometry"]["thermal_camera_preset"])
        V["dist"].set(str(cfg["geometry"]["object_distance_m"] or ""))
        V["gsd"].set(str(cfg["geometry"]["gsd_override_mm_px"] or ""))
        V["palette"].set(cfg["thermal"]["palette"])
        V["tmin"].set(str(cfg["thermal"]["palette_tmin_c"]))
        V["tmax"].set(str(cfg["thermal"]["palette_tmax_c"]))
        V["interval"].set(str(cfg["processing"]["video_frame_interval_s"]))
        env = cfg["environment"]
        for k, ck in [("amb", "ambient_temp_c"), ("irr", "irradiance_wm2"), ("wind", "wind_speed_ms"), ("load", "load_pct")]:
            V[k].set("" if env.get(ck) is None else str(env[ck]))
    refresh_from_cfg()

    # ---- Step 1: asset type
    f1 = ttk.LabelFrame(run_tab, text=" STEP 1 - Select type of work / analysis ")
    f1.pack(fill="x", padx=8, pady=6)
    asset_frame = ttk.Frame(f1)
    asset_frame.pack(fill="x", padx=6, pady=4)

    def build_asset_buttons():
        for w in asset_frame.winfo_children():
            w.destroy()
        for i, (k, a) in enumerate(all_assets(cfg).items()):
            ttk.Radiobutton(asset_frame, text=a["label"], value=k, variable=V["asset"], style="Asset.TRadiobutton").grid(
                row=i // 3, column=i % 3, sticky="w", padx=6, pady=2)
    build_asset_buttons()

    # ---- Step 2: folders
    f2 = ttk.LabelFrame(run_tab, text=" STEP 2 - Select your data folder (photos / videos / thermal, sub-folders included) ")
    f2.pack(fill="x", padx=8, pady=6)

    def pick_in():
        d = filedialog.askdirectory(title="Select survey data folder")
        if d:
            V["inp"].set(d)
            if not V["out"].get():
                V["out"].set(str(Path(d) / f"_DroneInspect_Report_{dt.datetime.now():%Y%m%d_%H%M}"))

    def pick_out():
        d = filedialog.askdirectory(title="Select output folder")
        if d:
            V["out"].set(d)
    for r, (lbl, var, cmd) in enumerate([("Data folder", "inp", pick_in), ("Report folder", "out", pick_out)]):
        ttk.Label(f2, text=lbl, width=14).grid(row=r, column=0, sticky="w", padx=6, pady=3)
        ttk.Entry(f2, textvariable=V[var], width=95).grid(row=r, column=1, sticky="we", pady=3)
        ttk.Button(f2, text="Browse...", command=cmd).grid(row=r, column=2, padx=6)

    # ---- Step 3: optional details
    f3 = ttk.LabelFrame(run_tab, text=" STEP 3 (optional) - Project details & survey conditions ")
    f3.pack(fill="x", padx=8, pady=6)
    fields = [("Project", "project"), ("Asset / Structure ID", "asset_id"), ("Location", "location"), ("Client", "client"),
              ("Inspector / Pilot", "inspector"), ("Survey date", "date"),
              ("Camera-to-surface dist (m)", "dist"), ("GSD override (mm/px)", "gsd"), ("Video frame every (s)", "interval"),
              ("Ambient temp (°C)", "amb"), ("Irradiance (W/m²)", "irr"), ("Wind (m/s)", "wind"), ("Electrical load (%)", "load"),
              ("Thermal scale min (°C)", "tmin"), ("Thermal scale max (°C)", "tmax")]
    for i, (lbl, k) in enumerate(fields):
        ttk.Label(f3, text=lbl).grid(row=i // 3, column=(i % 3) * 2, sticky="e", padx=4, pady=2)
        ttk.Entry(f3, textvariable=V[k], width=22).grid(row=i // 3, column=(i % 3) * 2 + 1, sticky="w", pady=2)
    r = len(fields) // 3 + 1
    ttk.Label(f3, text="RGB camera").grid(row=r, column=0, sticky="e", padx=4)
    cam_cb = ttk.Combobox(f3, textvariable=V["preset"], width=30, values=["auto"] + list(cfg["camera_presets"]))
    cam_cb.grid(row=r, column=1, sticky="w")
    ttk.Label(f3, text="Thermal camera").grid(row=r, column=2, sticky="e", padx=4)
    tcam_cb = ttk.Combobox(f3, textvariable=V["tpreset"], width=30, values=["auto"] + list(cfg["camera_presets"]))
    tcam_cb.grid(row=r, column=3, sticky="w")
    ttk.Label(f3, text="Thermal palette").grid(row=r, column=4, sticky="e", padx=4)
    ttk.Combobox(f3, textvariable=V["palette"], width=12, values=["ironbow", "whitehot", "blackhot", "rainbow"]).grid(row=r, column=5, sticky="w")

    # ---- Step 4: run
    f4 = ttk.Frame(run_tab)
    f4.pack(fill="x", padx=8, pady=10)
    run_btn = ttk.Button(f4, text="▶  RUN ANALYSIS & GENERATE REPORT", style="Big.TButton")
    run_btn.pack(side="left")
    stop_btn = ttk.Button(f4, text="■ Stop", state="disabled")
    stop_btn.pack(side="left", padx=8)
    open_btn = ttk.Button(f4, text="Open Report", state="disabled")
    open_btn.pack(side="left", padx=4)
    folder_btn = ttk.Button(f4, text="Open Output Folder", state="disabled")
    folder_btn.pack(side="left", padx=4)
    pb = ttk.Progressbar(run_tab, mode="determinate", maximum=100)
    pb.pack(fill="x", padx=8)
    status = ttk.Label(run_tab, text="Ready - choose work type and data folder, then press RUN.")
    status.pack(fill="x", padx=8, pady=4)

    logbox = scrolledtext.ScrolledText(log_tab, font=("Consolas", 9))
    logbox.pack(fill="both", expand=True)

    # ---- Settings tab: full JSON editor
    ttk.Label(set_tab, text="Every threshold, band, camera, priority, remedy text and asset profile is editable below (JSON). "
                            "Add new asset types under \"custom_assets\"; override remedy text under \"knowledge_base_overrides\".",
              wraplength=980).pack(fill="x", padx=8, pady=6)
    editor = scrolledtext.ScrolledText(set_tab, font=("Consolas", 10), undo=True)
    editor.pack(fill="both", expand=True, padx=8)

    def show_cfg():
        editor.delete("1.0", "end")
        editor.insert("1.0", json.dumps(cfg, indent=2))

    def save_cfg():
        nonlocal cfg
        try:
            new = json.loads(editor.get("1.0", "end"))
        except json.JSONDecodeError as e:
            messagebox.showerror("Invalid JSON", str(e))
            return
        cfg = deep_merge(DEFAULT_CONFIG, new)
        save_config(cfg)
        refresh_from_cfg()
        build_asset_buttons()
        cam_cb["values"] = tcam_cb["values"] = ["auto"] + list(cfg["camera_presets"])
        messagebox.showinfo("Saved", f"Settings saved to\n{CONFIG_PATH}")

    def reset_cfg():
        nonlocal cfg
        if messagebox.askyesno("Reset", "Restore all factory defaults?"):
            cfg = copy.deepcopy(DEFAULT_CONFIG)
            save_config(cfg)
            show_cfg()
            refresh_from_cfg()
            build_asset_buttons()

    def export_cfg():
        p = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if p:
            Path(p).write_text(editor.get("1.0", "end"), encoding="utf-8")

    def import_cfg():
        p = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if p:
            editor.delete("1.0", "end")
            editor.insert("1.0", Path(p).read_text(encoding="utf-8"))
    bf = ttk.Frame(set_tab)
    bf.pack(fill="x", padx=8, pady=6)
    for t, c in [("Save settings", save_cfg), ("Reload", show_cfg), ("Import profile...", import_cfg),
                 ("Export profile...", export_cfg), ("Factory reset", reset_cfg)]:
        ttk.Button(bf, text=t, command=c).pack(side="left", padx=4)
    show_cfg()

    state = {"stop": None, "paths": None}

    def fnum(s):
        s = s.strip()
        return float(s) if s else None

    def start():
        if not V["inp"].get() or not Path(V["inp"].get()).is_dir():
            messagebox.showwarning("Data folder", "Please select a valid data folder.")
            return
        out = V["out"].get() or str(Path(V["inp"].get()) / f"_DroneInspect_Report_{dt.datetime.now():%Y%m%d_%H%M}")
        V["out"].set(out)
        run_cfg = copy.deepcopy(cfg)
        try:
            run_cfg["geometry"].update(camera_preset=V["preset"].get() or "auto", thermal_camera_preset=V["tpreset"].get() or "auto",
                                       object_distance_m=fnum(V["dist"].get()) or 0.0, gsd_override_mm_px=fnum(V["gsd"].get()) or 0.0)
            run_cfg["thermal"].update(palette=V["palette"].get(), palette_tmin_c=fnum(V["tmin"].get()) or 0.0,
                                      palette_tmax_c=fnum(V["tmax"].get()) or 100.0)
            run_cfg["processing"]["video_frame_interval_s"] = fnum(V["interval"].get()) or 2.0
            run_cfg["environment"].update(ambient_temp_c=fnum(V["amb"].get()), irradiance_wm2=fnum(V["irr"].get()),
                                          wind_speed_ms=fnum(V["wind"].get()), load_pct=fnum(V["load"].get()))
        except ValueError:
            messagebox.showerror("Input", "Numeric fields must contain numbers.")
            return
        project = {k: V[k].get() for k in ["project", "asset_id", "location", "client", "inspector", "date"]}
        state["stop"] = threading.Event()
        run_btn.state(["disabled"])
        stop_btn.state(["!disabled"])
        open_btn.state(["disabled"])
        folder_btn.state(["disabled"])
        logbox.delete("1.0", "end")

        def worker():
            try:
                eng = Engine(run_cfg, V["asset"].get(), V["inp"].get(), out, project,
                             log=lambda m: q.put(("log", m)), progress=lambda f, m="": q.put(("prog", (f, m))),
                             stop_event=state["stop"])
                q.put(("done", eng.run()))
            except Exception as e:
                q.put(("log", traceback.format_exc()))
                q.put(("err", str(e)))
        threading.Thread(target=worker, daemon=True).start()

    def poll():
        try:
            while True:
                kind, val = q.get_nowait()
                if kind == "log":
                    logbox.insert("end", val + "\n")
                    logbox.see("end")
                elif kind == "prog":
                    pb["value"] = val[0] * 100
                    status["text"] = val[1]
                elif kind in ("done", "err"):
                    run_btn.state(["!disabled"])
                    stop_btn.state(["disabled"])
                    if kind == "done":
                        state["paths"] = val
                        open_btn.state(["!disabled"])
                        folder_btn.state(["!disabled"])
                        status["text"] = f"Completed - report: {val['html']}"
                        if messagebox.askyesno("Finished", "Analysis complete.\nOpen the inspection report now?"):
                            open_path(val["html"])
                    else:
                        status["text"] = "Error: " + val
                        messagebox.showerror("Error", val)
        except queue.Empty:
            pass
        root.after(150, poll)

    run_btn["command"] = start
    stop_btn["command"] = lambda: state["stop"] and state["stop"].set()
    open_btn["command"] = lambda: state["paths"] and open_path(state["paths"]["html"])
    folder_btn["command"] = lambda: open_path(V["out"].get())
    poll()
    root.mainloop()


# =============================================================================
# 11. CLI
# =============================================================================
def main():
    ap = argparse.ArgumentParser(description=f"{APP_NAME} - UAV inspection analytics")
    ap.add_argument("--cli", action="store_true", help="run without GUI")
    ap.add_argument("--asset", help="asset key (see --list-assets)")
    ap.add_argument("--input", help="data folder")
    ap.add_argument("--output", help="output folder")
    ap.add_argument("--config", help="alternative config JSON")
    ap.add_argument("--project", default="")
    ap.add_argument("--asset-id", default="")
    ap.add_argument("--location", default="")
    ap.add_argument("--client", default="")
    ap.add_argument("--inspector", default="")
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--gsd", type=float, help="GSD override mm/px (RGB)")
    ap.add_argument("--thermal-gsd", type=float, help="GSD override mm/px (thermal)")
    ap.add_argument("--distance", type=float, help="camera-to-surface distance m")
    ap.add_argument("--ambient", type=float)
    ap.add_argument("--irradiance", type=float)
    ap.add_argument("--wind", type=float)
    ap.add_argument("--load", type=float, help="electrical load %% of rating")
    ap.add_argument("--palette", choices=["ironbow", "whitehot", "blackhot", "rainbow"])
    ap.add_argument("--tmin", type=float)
    ap.add_argument("--tmax", type=float)
    ap.add_argument("--list-assets", action="store_true")
    ap.add_argument("--write-default-config", action="store_true", help=f"write defaults to {CONFIG_PATH}")
    a = ap.parse_args()
    cfg = load_config(a.config) if a.config else load_config()
    if a.write_default_config:
        save_config(copy.deepcopy(DEFAULT_CONFIG))
        print("Written", CONFIG_PATH)
        return
    if a.list_assets:
        for k, v in all_assets(cfg).items():
            print(f"{k:16s} {v['label']}")
        return
    if not a.cli:
        try:
            run_gui()
            return
        except ImportError:
            print("Tkinter not available - use --cli (see --help).")
            return
    if not (a.asset and a.input):
        ap.error("--cli requires --asset and --input")
    if a.asset not in all_assets(cfg):
        ap.error(f"unknown asset '{a.asset}'. Use --list-assets")
    g, t, env = cfg["geometry"], cfg["thermal"], cfg["environment"]
    if a.gsd: g["gsd_override_mm_px"] = a.gsd
    if a.thermal_gsd: g["thermal_gsd_override_mm_px"] = a.thermal_gsd
    if a.distance: g["object_distance_m"] = a.distance
    if a.palette: t["palette"] = a.palette
    if a.tmin is not None: t["palette_tmin_c"] = a.tmin
    if a.tmax is not None: t["palette_tmax_c"] = a.tmax
    for k, v in [("ambient_temp_c", a.ambient), ("irradiance_wm2", a.irradiance), ("wind_speed_ms", a.wind), ("load_pct", a.load)]:
        if v is not None:
            env[k] = v
    out = a.output or str(Path(a.input) / f"_DroneInspect_Report_{dt.datetime.now():%Y%m%d_%H%M}")
    project = {"project": a.project, "asset_id": a.asset_id, "location": a.location, "client": a.client,
               "inspector": a.inspector, "date": a.date}
    eng = Engine(cfg, a.asset, a.input, out, project,
                 progress=lambda f, m="": print(f"[{f * 100:5.1f}%] {m}"))
    paths = eng.run()
    rb = ReportBuilder(eng)
    print(f"\nHealth index {rb.hi}/100 - {rb.rating}\nFindings: {rb.counts()}")
    for k, p in paths.items():
        print(f"{k.upper():5s}: {p}")


if __name__ == "__main__":
    main()
