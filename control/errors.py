from fastapi import HTTPException


class ApiError(HTTPException):
    """Error con el formato { error: { code, message, details? } }."""

    def __init__(self, status_code: int, code: str, message: str, details: list | None = None):
        super().__init__(status_code=status_code, detail=message)
        self.code = code
        self.details = details
