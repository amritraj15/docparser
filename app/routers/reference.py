from fastapi import APIRouter

from app.services.extraction import SEGMENT_OPTIONS, IMPACT_AREA_OPTIONS

router = APIRouter(prefix="/reference", tags=["reference"])


@router.get("/segments")
def list_segments():
    """
    The controlled vocabulary the model is constrained to when classifying `segment`.
    Exposed so a frontend can render this as a dropdown instead of hardcoding it — and so
    there's exactly one place (SEGMENT_OPTIONS in extraction.py) that defines the list,
    rather than the API and the model schema drifting apart over time.
    """
    return {"segments": SEGMENT_OPTIONS}


@router.get("/impact-areas")
def list_impact_areas():
    return {"impact_areas": IMPACT_AREA_OPTIONS}
