from pydantic import BaseModel, EmailStr


class ResendVerificationRequest(BaseModel):
    """メール確認再送信リクエスト"""

    email: EmailStr
