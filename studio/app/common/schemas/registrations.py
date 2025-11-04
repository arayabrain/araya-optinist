from pydantic import BaseModel, EmailStr, validator


class TemporaryRegistrationRequest(BaseModel):
    """仮登録リクエスト"""

    email: EmailStr


class MainRegistrationRequest(BaseModel):
    """本登録リクエスト"""

    token: str
    name: str
    password: str
    confirm_password: str
    organization_id: int
    role_id: int = None

    @validator("name")
    def validate_name(cls, v):
        if len(v.strip()) < 2:
            raise ValueError("名前は2文字以上で入力してください")
        return v.strip()

    @validator("password")
    def validate_password(cls, v):
        if len(v) < 8:
            raise ValueError("パスワードは8文字以上で入力してください")
        if not any(c.isupper() for c in v):
            raise ValueError("パスワードには大文字を含めてください")
        if not any(c.islower() for c in v):
            raise ValueError("パスワードには小文字を含めてください")
        if not any(c.isdigit() for c in v):
            raise ValueError("パスワードには数字を含めてください")
        return v

    @validator("confirm_password")
    def passwords_match(cls, v, values):
        if "password" in values and v != values["password"]:
            raise ValueError("パスワードが一致しません")
        return v


class VerifyTokenRequest(BaseModel):
    """トークン検証リクエスト"""

    token: str
