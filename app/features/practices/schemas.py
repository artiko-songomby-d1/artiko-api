from pydantic import BaseModel, ConfigDict

from app.features.practices.models import Practice
from app.features.practices.status import Status, status
from app.features.works.categories import CategoryId


class PracticeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    region: str
    category: CategoryId
    practitioners: int
    learners: int
    median_age: int
    status: Status
    description: str

    @classmethod
    def of(cls, p: Practice) -> "PracticeOut":
        return cls(
            id=p.id, name=p.name, region=p.region, category=p.category,
            practitioners=p.practitioners, learners=p.learners, median_age=p.median_age,
            status=status(p.practitioners, p.learners, p.median_age), description=p.description,
        )
