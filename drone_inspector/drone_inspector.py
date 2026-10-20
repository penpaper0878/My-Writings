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
RUN GUI  : python drone_inspector.py
RUN CLI  : python drone_inspector.py --cli --asset bridge --input D:/survey --output D:/out
           python drone_inspector.py --list-assets
CUSTOMISE: Settings tab (JSON editor) or ~/.droneinspect/config.json. Every
           threshold, band, camera preset, priority/timeframe, knowledge-base
           text and asset profile is overridable; new asset profiles go under
           "custom_assets" (example "lattice_tower" shipped).

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
VERSION = "1.0.0"
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
        "include_media_appendix": True,
    },
    "processing": {
        "max_process_side_px": 2400,       # images are downscaled to this for CV (GSD rescaled)
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
        "DJI Zenmuse H20T thermal": {"match": ["ZH20T"], "sensor_w_mm": 7.68, "focal_mm": 13.5, "width_px": 640, "thermal": True},
        "DJI Matrice 30T thermal": {"match": ["M30T"], "sensor_w_mm": 7.68, "focal_mm": 9.1, "width_px": 640, "thermal": True},
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
        },
        "corrosion": {"hsv_lo": [3, 90, 40], "hsv_hi": [22, 255, 210], "min_area_pct": 0.05,
                      # ISO 4628-3 Ri grades: rusted area %
                      "ri_limits_pct": [0.05, 0.5, 1.0, 8.0, 40.0],
                      "ri_severity": ["Low", "Low", "Low", "Medium", "High", "Critical"]},
        "efflorescence": {"s_max": 28, "v_min": 205, "bands_pct": [[0.5, "Low"], [2.0, "Medium"], [6.0, "High"]]},
        "vegetation": {"exg_min": 0.06, "bands_pct": [[1.0, "Low"], [5.0, "Medium"], [15.0, "High"], [35.0, "Critical"]]},
        "spall": {"local_drop": 38, "min_area_pct": 0.03, "max_area_pct": 12.0, "max_solidity": 0.88,
                  "bands_pct": [[0.0, "Medium"], [1.0, "High"], [4.0, "Critical"]]},
        "moisture": {"v_max": 80, "s_max": 60, "min_area_pct": 1.5, "bands_pct": [[1.5, "Low"], [5.0, "Medium"], [15.0, "High"]]},
        "soiling": {"bands_pct": [[3.0, "Low"], [8.0, "Medium"], [15.0, "High"], [30.0, "Critical"]]},
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
        "description": "Rust-coloured area segmented in HSV; graded to ISO 4628-3 rust scale Ri0-Ri5 (0, 0.05, 0.5, 1, 8, 40-50 % rusted area). % is relative to image frame unless member fills frame - may under-estimate.",
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
        "description": "Vegetation cover detected in corridor imagery. Height/clearance to conductor cannot be measured from single 2D images - confirm with LiDAR or photogrammetric DSM.",
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
        "description": "Vegetation detected between/around module rows.",
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
        "description": "Fraction of detected module area with soiling-like colour signature (brown/grey high-value pixels on dark cells).",
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
        "detectors": ["quality", "solar", "vegetation", "corrosion"],
        "thermal_mode": "pv",
        "map": {"vegetation": "vegetation_solar", "corrosion": "corrosion_steel"},
        "components": ["PV modules", "Module mounting structure / trackers", "String cables & MC4 connectors", "String combiner boxes / SCBs",
                       "Inverters", "Transformers / switchyard", "Drainage & roads", "Perimeter fence & vegetation"],
        "manual_checklist": ["Irradiance (>= 600 W/m2), module temperature, wind, cloud cover at scan time (IEC TS 62446-3)",
                             "String-level IV curves on anomalous strings", "Torque of structure bolts, tracker alignment",
                             "Cable management (sagging, UV damage, rodent damage)", "Earthing continuity & lightning protection", "Inverter / SCB thermal scan (handheld)"],
        "frequency": "Aerial IR: annually (and at commissioning / warranty end); cleaning per soiling rate; vegetation monthly in monsoon",
        "references": ["IEC TS 62446-3:2017", "IEC 62446-1", "IEC 61724-1", "IEC 61215 / 61730", "MNRE / CEA guidelines"],
    },
    "road": {
        "label": "Road / Highway / Runway Pavement",
        "detectors": ["quality", "crack", "spall", "vegetation", "moisture"],
        "thermal_mode": "civil_warm",
        "map": {"crack": "crack_pavement", "spall": "pothole", "vegetation": "vegetation_structure"},
        "components": ["Carriageway", "Paved / earthen shoulders", "Median & kerbs", "Side drains & culverts", "Road furniture & markings", "Embankment slopes"],
        "manual_checklist": ["Rutting depth (straight-edge)", "Roughness IRI", "Edge drop", "Markings retro-reflectivity", "Drain silting"],
        "frequency": "Routine monthly; condition survey bi-annual; structural evaluation every 3-5 yr",
        "references": ["IRC:82-2015", "IRC:81", "IRC:115", "IRC:SP:16 (roughness)", "ASTM D6433"],
    },
    "building": {
        "label": "Building / Facade / Industrial Structure",
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
          "rel_alt": None, "gimbal_pitch": None, "focal_mm": None, "lrf_m": None}
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
                         ("GimbalPitchDegree", "gimbal_pitch"), ("LRFTargetDistance", "lrf_m")]:
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


def detect_cracks(gray, gsd, c, roi=None, bright=False):
    """Returns (list of crack dicts, mask). gsd in mm/px of the processed image or None."""
    g = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(cv2.medianBlur(gray, 5))  # suppress aggregate texture
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
        ang = 0.0
        if len(xs) > 2:
            cov = np.cov(np.vstack([xs, ys]).astype(np.float64))
            ev, evec = np.linalg.eigh(cov)
            vx, vy = evec[:, 1]
            ang = abs(math.degrees(math.atan2(vy, vx)))
            ang = 180 - ang if ang > 90 else ang
        cracks.append({"bbox": [int(x), int(y), int(w), int(h)], "length_px": L,
                       "w_med_px": float(np.median(wv)), "w_max_px": float(np.percentile(wv, 95)),
                       "angle": ang, "junctions": junc})
        keep[y:y + h, x:x + w] |= comp
    for cr in cracks:
        if gsd:
            cr["length_m"] = cr["length_px"] * gsd / 1000.0
            cr["w_med_mm"] = cr["w_med_px"] * gsd
            cr["w_max_mm"] = cr["w_max_px"] * gsd
    return cracks, keep


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
    # remove sky / large uniform bright areas touching top border
    n, lab, st, _ = cv2.connectedComponentsWithStats(m, 8)
    H, W = m.shape
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if (y == 0 and a > 0.02 * H * W) or a > 0.25 * H * W:
            m[lab == i] = 0
            continue
        # efflorescence is textured; uniform painted surfaces/sky are smooth
    return m


def spall_mask(gray, c, exclude=None):
    H, W = gray.shape
    k = max(31, (max(H, W) // 30) | 1)
    local = cv2.blur(gray.astype(np.float32), (k, k))
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
        if not (c["min_area_pct"] <= pct <= c["max_area_pct"]):
            continue
        if max(w, h) / max(1, min(w, h)) > 4:            # elongated -> crack/shadow edge, not spall
            continue
        comp = (lab[y:y + h, x:x + w] == i).astype(np.uint8)
        cnts, _ = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        hull = cv2.convexHull(max(cnts, key=cv2.contourArea))
        solidity = a / max(cv2.contourArea(hull), 1)
        if solidity <= c["max_solidity"]:
            out[y:y + h, x:x + w] |= comp
    return out


def moisture_mask(bgr, c, exclude=None):
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    m = ((hsv[..., 2] <= c["v_max"]) & (hsv[..., 1] <= c["s_max"])).astype(np.uint8)
    if exclude is not None:
        m[exclude > 0] = 0
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))
    return mask_components(m, int(c["min_area_pct"] / 100.0 * m.size * 0.2))


def pv_panel_mask(bgr):
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    m = ((v < 150) & (((h >= 90) & (h <= 135) & (s > 40)) | ((s < 60) & (v < 110)))).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
    return mask_components(m, int(0.005 * m.size))


def soiling_mask(bgr, panel):
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    dust = ((h >= 5) & (h <= 35) & (s >= 30) & (v > 80)) | ((s < 40) & (v > 150))
    m = (dust & (panel > 0)).astype(np.uint8)
    return cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))


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


def analyze_thermal(T, mode, cfg, gsd_mm):
    """Returns (list of anomaly dicts with severity, stats dict). gsd_mm: thermal GSD or None."""
    tc, th, env = cfg["thermal"], cfg["thresholds"], cfg["environment"]
    T = np.where(np.isfinite(T), T, np.nanmedian(T)).astype(np.float32)
    Ts = cv2.GaussianBlur(T, (0, 0), tc["smoothing_sigma"]) if tc["smoothing_sigma"] > 0 else T
    H, W = Ts.shape
    stats = {"Tmin": float(Ts.min()), "Tmax": float(Ts.max()), "Tmed": float(np.median(Ts))}
    minpx = int(tc["min_anomaly_px"])
    out = []
    if mode == "electrical":
        bg = float(np.median(Ts))
        fg = Ts > bg + tc["electrical_fg_delta_c"]
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
        lo, hi = np.percentile(Ts, [10, 90])
        ref = float(np.median(Ts[(Ts >= lo) & (Ts <= hi)]))
        bands = th["thermal_pv"]
        comps = thermal_components((Ts - ref) > bands[0][0], Ts - ref, Ts, minpx)
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
                if pat in ("string", "multi_module"):   # energy-significant patterns
                    sev = max(sev, "High" if pat == "string" else "Medium", key=lambda s: SEV_RANK[s])
            cpt.update(severity=sev, ref=ref, pattern=pat)
            out.append(cpt)
        stats["ref"] = ref
        return out, stats
    # civil / seepage / building / pipeline : local anomaly vs smoothed background
    sig = max(5.0, tc["local_bg_fraction"] * max(H, W))
    bgm = cv2.GaussianBlur(Ts, (0, 0), sig)
    d = Ts - bgm
    bands = th[THERMAL_MODES[mode][1]]
    t0 = bands[0][0]
    if mode == "civil_warm":
        mask = d > t0
    elif mode == "seepage_cold":
        mask = d < -t0
    else:
        mask = np.abs(d) > t0
    mask = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    comps = thermal_components(mask, d, Ts, max(minpx, int(0.0005 * H * W)))
    for cpt in comps[:15]:
        sev = band(abs(cpt["dT"]), bands)
        if not sev:
            continue
        pct = 100.0 * cpt["area_px"] / (H * W)
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
        self.findings.append(Finding(fid, defect, severity, summary, src, metrics,
                                     md.get("lat"), md.get("lon"), conf, qty or {}, thumb))

    # ---------------------------------------------------------------- RGB
    def analyze_rgb(self, bgr, src, md, native_w):
        cfg, th, det = self.cfg, self.cfg["thresholds"], self.asset["detectors"]
        amap = self.asset.get("map", {})
        H0, W0 = bgr.shape[:2]
        s = min(1.0, cfg["processing"]["max_process_side_px"] / max(H0, W0))
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
        ann = bgr.copy()
        overlay = bgr.copy()
        n0 = len(self.findings)
        area_m2 = lambda px: px * (gsd / 1000.0) ** 2 if gsd else None
        veg = vegetation_mask(bgr, th["vegetation"]["exg_min"]) if ("vegetation" in det or "spall" in det or "moisture" in det) else None
        # --- PV visual (panel mask, soiling, glass damage)
        if "solar" in det:
            panel = pv_panel_mask(bgr)
            cover = 100.0 * panel.mean()
            rec["pv_panel_cover_pct"] = cover
            if cover > 3:
                soil = soiling_mask(bgr, panel)
                spct = 100.0 * soil.sum() / max(panel.sum(), 1)
                sev = band(spct, th["soiling"]["bands_pct"])
                overlay[soil > 0] = (0, 200, 255)
                if sev:
                    self.add("soiling_pv", sev, f"Soiling signature on {spct:.1f}% of detected module area", src, md,
                             {"soiled_pct_of_modules": round(spct, 2), "module_cover_pct": round(cover, 1)},
                             None, None, "Medium", {"area_m2": area_m2(soil.sum())})
                cr, cm = detect_cracks(gray, gsd, th["crack"], roi=cv2.erode(panel, np.ones((9, 9), np.uint8)), bright=True)
                broken = [c for c in cr if c["junctions"] >= 3]
                if broken:
                    b = max(broken, key=lambda c: c["junctions"])
                    overlay[cm > 0] = (255, 0, 255)
                    cv2.rectangle(ann, (b["bbox"][0], b["bbox"][1]), (b["bbox"][0] + b["bbox"][2], b["bbox"][1] + b["bbox"][3]), (255, 0, 255), 2)
                    self.add("pv_glass_damage", "High", f"{len(broken)} branched bright fracture pattern(s) inside module area", src, md,
                             {"patterns": len(broken), "max_junctions": b["junctions"]}, ann, b["bbox"], "Low")
        # --- cracks
        if "crack" in det:
            cr, cm = detect_cracks(gray, gsd, th["crack"])
            if cr:
                key = amap.get("crack", "crack_concrete")
                bands = th["crack"]["pavement_width_bands_mm" if key == "crack_pavement" else "width_bands_mm"]
                worst = max(cr, key=lambda c: c["w_max_px"])
                tot_px = sum(c["length_px"] for c in cr)
                junc = sum(c["junctions"] for c in cr)
                jd = 100.0 * junc / max(tot_px, 1)
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
                               "orientation": orient, "pattern": pattern, "gsd_mm_px": round(gsd, 3),
                               "min_resolvable_width_mm": round(gsd, 2)}
                    if bands[1][0] > 0 and gsd > bands[1][0]:
                        metrics["note"] = "GSD coarser than Medium-band width limit: finer cracks may be missed"
                    qty = {"length_m": tot_px * gsd / 1000}
                else:
                    sev = "Medium" if (tot_px > 0.5 * max(H, W) or pattern.startswith("map")) else "Low"
                    summ = f"{len(cr)} crack(s), total {tot_px:.0f} px, max width ~{worst['w_max_px']:.1f} px (no GSD), {orient}, {pattern}"
                    metrics = {"count": len(cr), "total_length_px": round(tot_px), "max_width_px": round(worst["w_max_px"], 1),
                               "orientation": orient, "pattern": pattern, "note": "width not quantified - set camera preset / GSD"}
                    qty = {}
                if pattern.startswith("map") and SEV_RANK[sev] < 1:
                    sev = "Medium"
                if key == "crack_concrete" and orient == "diagonal" and SEV_RANK[sev] >= 1:
                    metrics["diagnostic_hint"] = "diagonal cracking - check for shear distress near supports"
                overlay[cm > 0] = (0, 0, 255)
                b = worst["bbox"]
                cv2.rectangle(ann, (b[0], b[1]), (b[0] + b[2], b[1] + b[3]), (0, 0, 255), 2)
                self.add(key, sev, summ, src, md, metrics, ann, b, "Medium", qty)
            else:
                cm = None
        # --- corrosion / rust staining
        rust = None
        if "corrosion" in det:
            c = th["corrosion"]
            rust = color_mask_hsv(bgr, c["hsv_lo"], c["hsv_hi"])
            if veg is not None:
                rust[veg > 0] = 0
            pct = 100.0 * rust.mean()
            if pct >= c["min_area_pct"]:
                lim = c["ri_limits_pct"]
                ri = sum(pct >= x for x in lim)
                sev = c["ri_severity"][ri]
                overlay[rust > 0] = (0, 140, 255)
                key = amap.get("corrosion", "corrosion_steel")
                self.add(key, sev, f"Rust-coloured area {pct:.2f}% of frame -> ISO 4628-3 Ri{ri}", src, md,
                         {"rust_area_pct": round(pct, 3), "iso4628_3": f"Ri{ri}"}, ann, bbox_of(rust), "Medium",
                         {"area_m2": area_m2(rust.sum())})
        # --- efflorescence
        if "efflorescence" in det:
            ef = efflorescence_mask(bgr, th["efflorescence"])
            pct = 100.0 * ef.mean()
            sev = band(pct, th["efflorescence"]["bands_pct"])
            if sev:
                overlay[ef > 0] = (255, 255, 0)
                self.add("efflorescence", sev, f"White deposits / leaching {pct:.2f}% of frame", src, md,
                         {"area_pct": round(pct, 3)}, ann, bbox_of(ef), "Low", {"area_m2": area_m2(ef.sum())})
        # --- spalling / pothole
        if "spall" in det:
            excl = np.zeros_like(gray, np.uint8)
            if veg is not None:
                excl |= veg
            sp = spall_mask(gray, th["spall"], excl)
            pct = 100.0 * sp.mean()
            if sp.any():
                sev = band(pct, th["spall"]["bands_pct"], "Medium")
                if rust is not None and (cv2.dilate(sp, np.ones((15, 15), np.uint8)) & rust).any():
                    sev = escalate(sev)                       # spall + rust = exposed corroding rebar
                overlay[sp > 0] = (255, 0, 255)
                key = amap.get("spall", "spalling")
                bb = bbox_of(sp)
                cv2.rectangle(ann, (bb[0], bb[1]), (bb[0] + bb[2], bb[1] + bb[3]), (255, 0, 255), 2)
                self.add(key, sev, f"Dark irregular void/spall-like regions {pct:.2f}% of frame", src, md,
                         {"area_pct": round(pct, 3)}, ann, bb, "Low", {"area_m2": area_m2(sp.sum())})
        # --- vegetation
        if "vegetation" in det and veg is not None:
            pct = 100.0 * veg.mean()
            sev = band(pct, th["vegetation"]["bands_pct"])
            if sev:
                overlay[veg > 0] = (0, 200, 0)
                self.add(amap.get("vegetation", "vegetation_structure"), sev, f"Vegetation cover {pct:.1f}% of frame", src, md,
                         {"vegetation_pct": round(pct, 2)}, ann, bbox_of(veg), "High", {"area_m2": area_m2(veg.sum())})
        # --- moisture
        if "moisture" in det:
            mo = moisture_mask(bgr, th["moisture"], veg)
            pct = 100.0 * mo.mean()
            sev = band(pct, th["moisture"]["bands_pct"])
            if sev:
                overlay[mo > 0] = (255, 120, 0)
                self.add("moisture", sev, f"Dark wet-patch-like regions {pct:.1f}% of frame (verify)", src, md,
                         {"area_pct": round(pct, 2)}, ann, bbox_of(mo), "Low", {"area_m2": area_m2(mo.sum())})
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
        anomalies, stats = analyze_thermal(T, mode, cfg, gsd)
        rec = {"source": src, "kind": "thermal", "w": W, "h": H, "gsd_mm": gsd, "gsd_basis": basis,
               "lat": md.get("lat"), "lon": md.get("lon"), "rel_alt": md.get("rel_alt"), "model": md.get("model"),
               "datetime": md.get("datetime"), "flags": [] if radiometric else ["non-radiometric (palette)"],
               "thermal_source": how, **{k: round(v, 2) for k, v in stats.items()}}
        if not (-40 < stats["Tmed"] < 200):
            rec["flags"].append("implausible temperatures - check TIFF scale/offset")
        ann = colorize_T(T)
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
                        self.analyze_rgb(img, src, read_metadata(f), img.shape[1])
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
def health_index(findings, n_media, cfg):
    w = cfg["severity_weights"]
    pen = sum(w[f.severity] for f in findings) / max(1, n_media)
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
        self.f = sorted(eng.findings, key=lambda x: (-SEV_RANK[x.severity], x.defect, x.source))
        self.hi, self.rating = health_index(eng.findings, len(eng.media), eng.cfg)
        self.by_defect = {}
        for x in self.f:
            self.by_defect.setdefault(x.defect, []).append(x)

    def counts(self):
        return {s: sum(1 for x in self.f if x.severity == s) for s in SEVERITIES}

    def quantities(self):
        q = {}
        for k, lst in self.by_defect.items():
            agg = {}
            for x in lst:
                for qk, qv in x.quantity.items():
                    if qv is not None:
                        agg[qk] = agg.get(qk, 0) + qv
            q[k] = agg
        return q

    def acquisition_qa(self):
        env, msgs = self.cfg["environment"], []
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
        L.append(f"<div class='kpi'>Health index<b>{self.hi}/100</b></div><div class='kpi'>Media analysed<b>{len(e.media)}</b></div>"
                 f"<div class='kpi'>Findings<b>{len(self.f)}</b></div>" +
                 "".join(f"<div class='kpi' style='border-left:6px solid {SEV_COLOR[s]}'>{s}<b>{c[s]}</b></div>" for s in reversed(SEVERITIES)))
        L.append(f"<p><b>Overall condition rating:</b> {esc(self.rating)}</p>")
        L.append(self.svg_bar(c))
        if self.f:
            L.append("<p><b>Principal findings (highest severity per defect type):</b></p><ul>")
            for k, lst in sorted(self.by_defect.items(), key=lambda kv: -SEV_RANK[kv[1][0].severity]):
                L.append(f"<li>{sev(lst[0].severity)} <b>{esc(kb_entry(cfg, k)['name'])}</b> - {len(lst)} occurrence(s); worst: {esc(lst[0].summary)} [{esc(lst[0].source)}]</li>")
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
        meth = [
            "Image QA: variance-of-Laplacian sharpness (normalised 1000 px), mean-luminance exposure check.",
            "GSD = sensor width x distance / (focal length x image width); distance = user object distance, LRF, or relative altitude (slant-corrected by gimbal pitch).",
            f"Cracks: CLAHE + morphological black-hat (k={th['crack']['blackhat_kernel_px']} px), Otsu threshold, elongation & length filters, thinning skeleton; width = 2·d-1 from Euclidean distance transform (95th percentile). Bands (mm): {th['crack']['width_bands_mm']}; pavements {th['crack']['pavement_width_bands_mm']}.",
            f"Corrosion: HSV segmentation {th['corrosion']['hsv_lo']}-{th['corrosion']['hsv_hi']}, graded to ISO 4628-3 Ri (limits {th['corrosion']['ri_limits_pct']} % area).",
            f"Vegetation: Excess-Green index (2g-r-b)/(r+g+b) > {th['vegetation']['exg_min']}; bands % frame {th['vegetation']['bands_pct']}.",
            "Spall/void/pothole: local-contrast dark regions (box-filter background), solidity and aspect filters; escalated when adjacent to rust.",
            "Efflorescence: low saturation / high value segmentation with sky suppression. Moisture: low value & saturation, vegetation excluded (low confidence).",
            "Thermal: radiometric data used where available (TIFF/CSV/dji_irp); palette images inverted via LUT using operator-entered scale (indicative).",
            {"electrical": f"Electrical thermal criteria NETA MTS Table 100.18 - component-to-component {th['thermal_electrical_c2c']}; over-ambient {th['thermal_electrical_amb']} (C), optional load correction.",
             "pv": f"PV thermal: dT vs robust array reference, bands {th['thermal_pv']} K; pattern by anomaly area / module area ({cfg['pv']['module_length_m']} x {cfg['pv']['module_width_m']} m); IEC TS 62446-3 CoA classes.",
             }.get(e.asset.get("thermal_mode"), f"Thermal ({e.asset.get('thermal_mode')}): local anomaly vs Gaussian background (sigma {cfg['thermal']['local_bg_fraction']} x frame), bands {th.get(THERMAL_MODES.get(e.asset.get('thermal_mode'), ('', 'thermal_civil_warm'))[1], '')}."),
            f"Health index: 100 x exp(-P/10), P = (sum of severity weights {cfg['severity_weights']} / media count); capped at 39 if any Critical and 69 if any High. Rating bands are indicative.",
        ]
        L += [f"<li>{esc(m)}</li>" for m in meth] + ["</ul>"]
        # 4 Defect-wise analysis
        L.append("<h2>4. Defect-wise Analysis, Solutions &amp; Improvement Measures</h2>")
        for k, lst in sorted(self.by_defect.items(), key=lambda kv: -SEV_RANK[kv[1][0].severity]):
            kb = kb_entry(cfg, k)
            pr = cfg["priority"][lst[0].severity]
            L.append(f"<div class='card'><h3>{esc(kb['name'])} &nbsp; {sev(lst[0].severity)} &nbsp; <span class='small'>{len(lst)} occurrence(s) - Priority {pr[0]}</span></h3>")
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
                locs = ", ".join(f"{x.fid} ({x.source})" for x in sub[:8]) + (f" ... +{len(sub) - 8}" if len(sub) > 8 else "")
                L.append(f"<tr><td>{i}</td><td>{sev(s)} {pr[0]}</td><td>{esc(kb['name'])}</td><td>{esc(pr[1])}</td><td>{esc('; '.join(act))}</td><td class='small'>{esc(locs)}</td></tr>")
        L.append("</table>")
        # 6 Findings register
        L.append("<h2>6. Findings Register</h2><table><tr><th>ID</th><th>Severity</th><th>Defect</th><th>Source</th><th>GPS</th><th>Details</th><th>Image</th></tr>")
        for x in self.f[:cfg["report"]["max_register_rows"]]:
            gps = f"{x.lat:.6f}, {x.lon:.6f}" if x.lat is not None and x.lon is not None else "-"
            met = "<br>".join(f"{esc(k)}: {esc(v)}" for k, v in x.metrics.items() if k != "annotated")
            img = f"<img class='thumb' src='data:image/jpeg;base64,{x.thumb}'>" if x.thumb else ""
            L.append(f"<tr><td>{x.fid}</td><td>{sev(x.severity)}<br><span class='small'>conf. {x.confidence}</span></td><td>{esc(kb_entry(cfg, x.defect)['name'])}</td>"
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
                  "Percentages are of the image frame unless stated; true member-level extent requires orthomosaic / 3D model mapping.",
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
            d.add_paragraph(f"[{lst[0].severity}] {kb_entry(cfg, k)['name']} - {len(lst)} occurrence(s); worst: {lst[0].summary}", style="List Bullet")
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
            w.writerow(["id", "severity", "priority", "defect_key", "defect", "source", "lat", "lon", "confidence", "summary", "metrics", "quantity"])
            for x in self.f:
                w.writerow([x.fid, x.severity, self.cfg["priority"][x.severity][0], x.defect, kb_entry(self.cfg, x.defect)["name"], x.source,
                            x.lat, x.lon, x.confidence, x.summary, json.dumps(x.metrics), json.dumps(x.quantity)])
        with open(out / "media_index.csv", "w", newline="", encoding="utf-8") as fh:
            keys = ["source", "kind", "w", "h", "gsd_mm", "gsd_basis", "lat", "lon", "rel_alt", "model", "datetime", "blur", "brightness", "flags", "n_findings", "annotated"]
            w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            for m in self.e.media:
                w.writerow({**m, "flags": ";".join(m["flags"])})
        js = {"project": self.e.project, "asset": self.e.asset_key, "health_index": self.hi, "rating": self.rating,
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
