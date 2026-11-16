"""Session booking facts the LLM must not forget or re-ask."""

from __future__ import annotations

from typing import Any, Optional

# Spoken after confirmation when the patient tries to change anything.
_POST_CONFIRM_REPLIES = (
    (
        "Your booking is already confirmed. Your reference is {ref}. "
        "Changing details after confirmation is not available."
    ),
    (
        "This appointment is locked with reference {ref}. "
        "Re-editing the booking is not available on this call."
    ),
    (
        "Booking confirmed — reference {ref}. "
        "I cannot change name, phone, doctor, or time after confirmation."
    ),
    (
        "Your appointment is already booked. Reference {ref}. "
        "Updates are not available once the booking is confirmed."
    ),
)


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
        "offered_slots",
        "offered_doctors",
        "booking_reference",
        "_post_confirm_reply_index",
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
        self.offered_slots: list[Any] = []
        self.offered_doctors: list[dict[str, Any]] = []
        self.booking_reference: Optional[str] = None
        self._post_confirm_reply_index: int = 0

    def is_confirmed(self) -> bool:
        return bool(self.booking_reference)

    def post_confirm_reply(self) -> str:
        """Natural variation of the locked-booking message."""
        ref = self.booking_reference or "unknown"
        template = _POST_CONFIRM_REPLIES[
            self._post_confirm_reply_index % len(_POST_CONFIRM_REPLIES)
        ]
        self._post_confirm_reply_index += 1
        return template.format(ref=ref)

    def next_missing(self) -> str:
        if self.booking_reference:
            return "done"
        if not self.patient_name:
            return "name"
        if not self.phone:
            return "phone"
        if not self.reason:
            return "reason"
        if self.specialty_id is None:
            return "specialty"
        if self.doctor_id is None:
            return "doctor"
        if not self.slot_start:
            return "slot"
        return "confirm"

    def snapshot_message(self) -> str:
        """Compact system reminder for the current LLM turn."""
        if self.booking_reference:
            return (
                "BOOKING ALREADY CONFIRMED. reference={}. "
                "The appointment is locked. Re-editing is NOT available. "
                "If the patient asks to change name, phone, doctor, time, or reason, "
                "politely refuse and repeat that the booking is confirmed. "
                "Do NOT ask questions. Do NOT call tools. Do NOT collect new details.".format(
                    self.booking_reference
                )
            )

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
            parts.append(f"chosen_slot={self.slot_start}")
        if self.offered_doctors:
            labeled = []
            for i, doc in enumerate(self.offered_doctors[:5], start=1):
                labeled.append(f"{i}={doc.get('name')}")
            parts.append("offered_doctors[" + "; ".join(labeled) + "]")
        if self.offered_slots:
            labeled = []
            for i, slot in enumerate(self.offered_slots[:8], start=1):
                if isinstance(slot, dict):
                    start = slot.get("starts_at") or slot.get("start") or ""
                    label = slot.get("label") or start
                else:
                    label = str(slot)
                labeled.append(f"{i}={label}")
            parts.append("offered_slots[" + "; ".join(labeled) + "]")

        nxt = self.next_missing()
        guidance = {
            "name": "Ask only for the patient's full name. Do not ask for phone yet.",
            "phone": "Ask only for the phone number. Accept spoken digits.",
            "reason": "Ask only for the reason for the visit. Phone is already known — never ask for phone again.",
            "specialty": (
                "Call match_specialty with the reason. "
                "If still unclear after one clarifier, default to General Practice. "
                "Never re-ask name or phone."
            ),
            "doctor": (
                "Call find_doctors (no arguments). Then ask which doctor by name or list number. "
                "Never re-ask name or phone."
            ),
            "slot": (
                "Call check_availability with date=YYYY-MM-DD for the selected doctor. "
                "If that day is closed/empty, say so and offer the next real open slots. "
                "When the patient picks a list number, use that offered_slots starts_at. "
                "Never re-ask name or phone."
            ),
            "confirm": (
                "Read back name, phone, doctor, time, and reason once, then ask yes/no. "
                "Call create_appointment(confirmed=true) only after explicit yes. "
                "Never re-ask for phone — it is already {}.".format(self.phone or "saved")
            ),
        }.get(nxt, "Ask only for the next missing booking detail. Never re-ask saved fields.")

        if not parts:
            return (
                "KNOWN FACTS: none yet. Collect name, then phone, then reason. "
                "Do not skip ahead. Ask only the next missing item."
            )
        locked = []
        if self.phone:
            locked.append(
                "PHONE IS LOCKED at {}. NEVER ask for the phone number again.".format(
                    self.phone
                )
            )
        if self.patient_name:
            locked.append(
                "NAME IS LOCKED as {}. NEVER ask for the name again.".format(
                    self.patient_name
                )
            )
        return (
            "KNOWN FACTS (already collected — do NOT re-ask unless the patient corrects them): "
            + "; ".join(parts)
            + ". "
            + (" ".join(locked) + " " if locked else "")
            + "NEXT STEP: "
            + guidance
        )

    def apply_match(self, result: dict[str, Any]) -> None:
        if result.get("status") == "matched":
            if result.get("specialty_id") is not None:
                self.specialty_id = int(result["specialty_id"])
            if result.get("specialty_name"):
                self.specialty_name = str(result["specialty_name"])

    def apply_doctors(self, result: dict[str, Any]) -> None:
        doctors = result.get("doctors") or []
        self.offered_doctors = [d for d in doctors if isinstance(d, dict)]
        if len(self.offered_doctors) == 1:
            doc = self.offered_doctors[0]
            self.doctor_id = int(doc["id"])
            self.doctor_name = str(doc.get("name") or "")

    def remember_slots(self, slots: list[Any]) -> None:
        cleaned = [s for s in (slots or []) if s]
        if cleaned:
            self.offered_slots = list(cleaned[:8])

    def apply_availability(self, result: dict[str, Any], preferred: str = "") -> None:
        doctor = result.get("doctor") or {}
        if doctor.get("id") is not None:
            self.doctor_id = int(doctor["id"])
        if doctor.get("name"):
            self.doctor_name = str(doctor["name"])
        slots = result.get("alternatives") or result.get("slots") or []
        self.remember_slots(slots)
        if preferred and result.get("preferred_available") is True:
            self.slot_start = preferred

    def apply_booking(self, result: dict[str, Any]) -> None:
        if result.get("ok") and result.get("booking_reference"):
            self.booking_reference = str(result["booking_reference"])
            if result.get("starts_at"):
                self.slot_start = str(result["starts_at"])
