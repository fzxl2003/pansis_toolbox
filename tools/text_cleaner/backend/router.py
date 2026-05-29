from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter()


class CleanTextRequest(BaseModel):
    text: str = Field(default="", max_length=200_000)
    trim: bool = True
    collapseWhitespace: bool = True
    removeBlankLines: bool = True
    caseMode: str = "none"


class CleanTextResponse(BaseModel):
    originalLength: int
    cleanedLength: int
    text: str


@router.post("/clean", response_model=CleanTextResponse)
def clean_text(payload: CleanTextRequest) -> CleanTextResponse:
    text = payload.text

    if payload.removeBlankLines:
        text = "\n".join(line for line in text.splitlines() if line.strip())

    if payload.collapseWhitespace:
        text = "\n".join(" ".join(line.split()) for line in text.splitlines())

    if payload.trim:
        text = text.strip()

    if payload.caseMode == "lower":
        text = text.lower()
    elif payload.caseMode == "upper":
        text = text.upper()

    return CleanTextResponse(
        originalLength=len(payload.text),
        cleanedLength=len(text),
        text=text,
    )
