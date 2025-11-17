from pydantic import BaseModel, EmailStr, validator


class CompleteRegistrationRequest(BaseModel):
    """Registration completion request from frontend"""

    firebase_uid: str
    email: EmailStr
    name: str
    organization_id: int = 1
    role_id: int = None

    @validator("name")
    def validate_name(cls, v):
        if len(v.strip()) < 2:
            raise ValueError("Name must be at least 2 characters")
        return v.strip()


class VerifyTokenRequest(BaseModel):
    """Token verification request"""

    token: str
