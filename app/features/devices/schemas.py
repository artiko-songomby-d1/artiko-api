import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ChallengeOut(BaseModel):
    nonce: str
    expires_at: datetime


class DeviceIn(BaseModel):
    nonce: str = Field(max_length=200)
    # Base64 of the DER SubjectPublicKeyInfo of the P-256 key generated in the Android Keystore.
    public_key: str = Field(max_length=500)
    # Base64 DER certificates of the Key Attestation chain, leaf first.
    attestation_chain: list[str] = Field(max_length=10)


class DeviceOut(BaseModel):
    id: uuid.UUID
    attestation_level: str
