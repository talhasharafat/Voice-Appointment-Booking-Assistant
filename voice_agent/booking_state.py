"""Session booking facts the LLM must not forget or re-ask."""

from __future__ import annotations

from typing import Any, Optional


class BookingFacts:
    __slots__ = (
        "patient_name",
        "phone",
        "reason",
        "specialty_id",
        "specialty_name",
        "doctor_id",
        "doctor_name",
        "slot_start",
        "booking_reference",
    )

    def __init__(self) -> None:
        self.patient_name: Optional[str] = None
        self.phone: Optional[str] = None
        self.reason: Optional[str] = None
        self.specialty_id: Optional[int] = None
        self.specialty_name: Optional[str] = None
        self.doctor_id: Optional[int] = None
        self.doctor_name: Optional[str] = None
        self.slot_start: Optional[str] = None
        self.booking_reference: Optional[str] = None

    def snapshot_message(self) -> str:
        """Compact system reminder for the current LLM turn."""
        parts = []
        if self.patient_name:
            parts.append(f"name={self.patient_name}")
        if self.phone:
            parts.append(f"phone={self.phone}")
        if self.reason:
            parts.append(f"reason={self.reason}")
        if self.specialty_name:
            parts.append(f"specialty={self.specialty_name}")
        if self.doctor_name:
            parts.append(f"doctor={self.doctor_name}")
        if self.doctor_id is not None:
            parts.append(f"doctor_id={self.doctor_id}")
        if self.slot_start:
            parts.append(f"slot={self.slot_start}")
        if self.booking_reference:
            parts.append(f"reference={self.booking_reference}")
        if not parts:
            return (
                "KNOWN FACTS: none yet. Collect name, then phone, then reason. "
                "Do not skip ahead. Ask only the next missing item."
            )
        return (
            "KNOWN FACTS (already collected — do NOT re-ask unless the patient corrects them): "
            + "; ".join(parts)
            + ". Ask only for the next missing booking detail."
        )

    def apply_match(self, result: dict[str, Any]) -> None:
        if result.get("status") == "matched":
            if result.get("specialty_id") is not None:
                self.specialty_id = int(result["specialty_id"])
            if result.get("specialty_name"):
                self.specialty_name = str(result["specialty_name"])

    def apply_doctors(self, result: dict[str, Any]) -> None:
        doctors = result.get("doctors") or []
        if len(doctors) == 1:
            doc = doctors[0]
            self.doctor_id = int(doc["id"])
            self.doctor_name = str(doc.get("name") or "")

    def apply_availability(self, result: dict[str, Any], preferred: str = "") -> None:
        doctor = result.get("doctor") or {}
        if doctor.get("id") is not None:
            self.doctor_id = int(doctor["id"])
        if doctor.get("name"):
            self.doctor_name = str(doctor["name"])
        if preferred and result.get("preferred_available") is True:
            self.slot_start = preferred

    def apply_booking(self, result: dict[str, Any]) -> None:
        if result.get("ok") and result.get("booking_reference"):
            self.booking_reference = str(result["booking_reference"])
            if result.get("starts_at"):
                self.slot_start = str(result["starts_at"])
