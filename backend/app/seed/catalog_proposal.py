"""Proposal for the team review (phase 2 checkpoint): study attribute -> topic.

Car areas, topics and aspect groups come from the fixed taxonomy (``taxonomy/``,
see ``app/taxonomy.py``); they are not proposed here. Written by the
implementing agent (Claude Code) from the study sheets; it is a proposal, not a
decision. ``python -m app.seed.run --review`` writes the review CSVs to
``backend/seed/review/``. After the team confirms (column ``confirmed`` = yes,
or a corrected value) the seed loads the CSVs.
"""

from __future__ import annotations

import csv

from app import taxonomy
from app.config import BACKEND_DIR

REVIEW_DIR = BACKEND_DIR / "seed" / "review"
PROPOSED_BY = "agent:claude-code (seed proposal)"

# Customer study attribute -> topic ID of the taxonomy. None = no topic (overall scores).
STUDY_ATTRIBUTES: dict[str, str | None] = {
    "Overall product satisfaction": None,
    "Exterior styling": "exterior_design",
    "Interior styling": "interior_design",
    "Quality of interior and materials used": "upholstery_interior_trim",
    "Level of equipment": "body_equipment_overall_technology",
    "Safety": "impression_of_safety",
    "Freedom from faults/ squeaks/ rattles": "interior_acoustics",
    "Value for money": None,
    "Reliability": None,
    "All round visibility": None,
    "Easy to read and use instruments/ controls/ displays": "instrument_cluster",
    "Smartphone integration/ connectivity": "apple_carplay",
    "Features of information and entertainment system including in-car apps": "connecteddrive_and_infotainment_overall_technology",
    "Easy to use information and entertainment system including in-car apps": "operating_concept_operating_system",
    "Integrated navigation system": "navigation",
    "Overall seating comfort": "seats",
    "Interior storage for small items": "storage_compartments",
    "Acceleration": "drivetrain",
    "Roadholding": "handling_riding",
    "Fuel economy/ energy efficiency": None,
    "Braking": "braking",
    "Sportiness": "drive_driving_dynamics_and_chassis_overall_technology",
    "Prestige/ manufacturer reputation": None,
    "Manoeuvrability": "steering",
    "Quality of ride": "cushioning_damping_vehicle",
    "Front interior roominess": "space_spatial_impression",
    "Rear interior roominess": "space_spatial_impression",
    "Ease of loading/ unloading luggage": "luggage_compartment",
    "Luggage capacity": "luggage_compartment",
    "Ease of entry into and out of car": "vehicle_access_and_exit",
    "Environm. friendliness": None,
    "Heating/ ventilation/ air conditioning": "vehicle_climatization",
    "Overall build quality": "body_equipment_overall_technology",
    "Quietness when driving": "interior_acoustics",
    "Driving position": "seat_memory",
    "Connected services": "connecteddrive_services_smartphone_technology_overall",
    "Interior versatility": "luggage_compartment",
    # US_2025
    "Overall exterior styling": "exterior_design",
    "Appearance of tires/ wheels": "wheels_tires",
    "Exterior door handles": "outer_door_handle_front",
    "Headlight/ taillight design": "exterior_lighting",
    "Overall driving performance": "drive_driving_dynamics_and_chassis_overall_technology",
    "Ground clearance": "ground_clearance",
    "Riding comfort": "cushioning_damping_vehicle",
    "Turning radius": "steering",
    "Handling/Maneuverability": "handling_riding",
    "Overall power and pickup": "drivetrain",
    "Acceleration from stop": "drivetrain",
    "Passing capability": "drivetrain",
    "Overall interior styling/ aesthetics": "interior_design",
    "Interior trim and finish": "upholstery_interior_trim",
    "Interior lighting": "vehicle_interior_lighting",
    "Quality of seat materials": "upholstery_interior_trim",
    "Quality of interior materials": "upholstery_interior_trim",
    "Interior door handles": "door_finisher_panel_front",
    "Overall comfort of the seats": "seats",
    "Comfort of front seat": "seats",
    "Comfort of 2nd row seat": "seats",
    "Ease of front seat entry": "vehicle_access_and_exit",
    "Front seat roominess": "space_spatial_impression",
    "Ease of 2nd row seat entry": "vehicle_access_and_exit",
    "2nd row seat roominess": "space_spatial_impression",
    "Interior roominess": "space_spatial_impression",
    "Interior storage": "storage_compartments",
    "Trunk lid/ tailgate and liftgate operations": "trunk_lid_open_close",
    "Ease of loading/unloading cargo": "luggage_compartment",
    "Cargo capacity/usefulness": "luggage_compartment",
    "Instrument panel (IP)": "instrument_panel",
    "Cupholders": "cupholder",
    "Overall interior versatility/seat stowaway": "luggage_compartment",
    "Driver seat adjustability": "seat_memory",
    "Passenger seat adjustability": "seat_memory",
    "Overall performance of climate control system": "vehicle_climatization",
    "Heater performance": "vehicle_climatization",
    "Air conditioner performance": "vehicle_climatization",
    "Operation of heater/ AC controls": "air_conditioning_setting",
    "Overall performance of sound system": "audio_mode_sound",
    "Design and integration of speakers": "audio_mode_sound",
    "Audio clarity": "audio_mode_sound",
    "Bass performance": "audio_mode_sound",
    "Placement/ ease of reading and using controls": "operating_concept_operating_system",
    "Intuitiveness of controls": "operating_concept_operating_system",
    "Overall noise/vibration/harshness": "interior_acoustics",
    "Active noise cancellation": "interior_acoustics",
    "Freedom from squeaks and rattles": "interior_acoustics",
    "Sound of engine": "engine_acoustics_engine_sound",
    "Freedom from wind and road noise": "interior_acoustics",
    "Smoothness of transmission": "transmission",
    "Solid vehicle construction": "body_equipment_overall_technology",
    "Front visibility": None,
    "Rear visibility": None,
    "Headlight function/ brightness": "exterior_lighting",
    "Level of standard equipment": "body_equipment_overall_technology",
    "Gas or electric mileage (fuel economy)": None,
    "Overall driver assist features": "driver_assistance_automated_driving_overall_technology",
    "If applicable: Backup system (camera, sensors, etc.)": "rear_view_camera",
    "Safety monitoring features (blid spot warning, lane departure warning, etc)": "blind_spot_monitoring",
    "Adaptive driving assists (lane keeping, cruise, braking)": "active_cruise_control",
    "Overall driver-vehicle interaction (HMI)": "operating_concept_operating_system",
    "If applicable: Navigation system": "navigation",
    "Bluetooth/ smartphone pairing": "manage_pair_mobile_device_bluetooth",
    "WiFi hotspot in vehicle": "wifi_hotspot",
    "Mirroring of smartphone content on vehicle display": "apple_carplay",
    "Place for phone": "mobile_device_wireless_charging",
    "Phone charging": "mobile_device_wireless_charging",
    "Ease of reading and using screen interface": "touch_screen_operation",
    "Head-up Display": "head_up_display",
    "Position/ integration of displays in dashboard": "instrument_panel",
    "Menu structure of infotainment system": "operating_concept_operating_system",
    "Voice recognition": "voice_control_speech_recognition",
    "Voice recognition system": "voice_control_speech_recognition",
    "Size/ proportions": "exterior_design",
    "Sound of doors/ trunk/ hatch shutting": "body_equipment_overall_technology",
    "If applicable: 4WD/AWD performance": "handling_riding",
    "Off-Road capability": None,
    "Interior colors": "interior_design",
    "Overall interior workmanship": "upholstery_interior_trim",
    "Overall interior design / layout": "interior_design",
    "Passenger seating capacity": "space_spatial_impression",
    "Provides a sense of wellbeing": "interior_comfort",
    "Overall vehicle sound": "interior_acoustics",
    "Overall impression of durability/reliability": None,
    "Freedom from electrical problems": None,
    "Overall safety of the vehicle": "impression_of_safety",
    "Warranty coverage": None,
    "Technical innovations": None,
    "Overall value for money": None,
    "Price/deal offered": None,
    "Future trade-in or resale value": None,
    "Economical to own (cost of ownership)": None,
    "Affordable to buy": None,
    "Overall brand image": None,
    "Reputation/prestige of manufacturer": None,
    "Overall vehicle image": None,
    "Vehicle's Mobile App": "my_bmw_app_mini_app",
    "Overall environmental friendliness": None,
    "If applicable: Flat tire preparedness (spare, run flat, etc.)": "tirefit",
    "If applicable: Sunroof/ moonroof": "panorama_glass_roof",
    "If applicable: Ease of customization": "driver_profile",
    "Digital key": "digital_key",
}


def write_review_files(study_attrs: list[str], force: bool = False) -> dict[str, int]:
    """Write study_attributes.csv.

    An existing file holds the team's review and is never overwritten unless force=True.
    """
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    path = REVIEW_DIR / "study_attributes.csv"
    if not force and path.exists():
        return {"skipped": 1}
    topics = taxonomy.topics()
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["attribute", "proposed_topic", "proposed_by", "confirmed"])
        for attr in study_attrs:
            topic = STUDY_ATTRIBUTES.get(attr.strip())
            if topic and topic not in topics:
                raise ValueError(f"study attribute {attr!r} maps to unknown topic {topic!r}")
            w.writerow([attr.strip(), topic or "", PROPOSED_BY, ""])
    return {"study_attributes": len(study_attrs)}
