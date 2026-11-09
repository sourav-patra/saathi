"""Fixed Hindi reply templates (T0: no model needed).

Names come from local config (.env), never from the repo.
"""

from __future__ import annotations


def emergency_to_elder(caregiver: str) -> str:
    return (
        f"Aapka sandesh mila. {caregiver} ko turant bata diya hai.\n"
        "Agar haalat gambhir hai to abhi 112 (emergency) ya 108 (ambulance) par call karein.\n"
        "Paas mein kisi ko awaaz dein."
    )


def scam_to_elder(caregiver: str) -> str:
    return (
        "⚠️ Dhyaan dein: kisi ko bhi OTP, PIN, CVV ya bank details na batayein, "
        "aur kisi anjaan link par click na karein.\n"
        f"Maine {caregiver} ko bata diya hai. Woh dekh rahe hain."
    )


def ack_to_elder(caregiver: str) -> str:
    return f"Theek hai, aapka sandesh mil gaya 🙏 {caregiver} ko bata diya hai."


def asked_caregiver(caregiver: str) -> str:
    return f"{caregiver} se approve karwa raha hoon, thoda intezaar karein 🙏"
