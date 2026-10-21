from pydantic import BaseModel, Field, field_validator, model_validator


class LogDeleteRequest(BaseModel):
    ids: list[int] = Field(default_factory=list, max_length=500)
    delete_all: bool = False
    before_id: int | None = Field(default=None, ge=1)
    level: str | None = Field(default=None, max_length=20)
    keyword: str | None = Field(default=None, max_length=200)

    @field_validator("ids")
    @classmethod
    def validate_ids(cls, value: list[int]) -> list[int]:
        if any(identifier < 1 for identifier in value):
            raise ValueError("Log identifiers must be positive")
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def validate_scope(self):
        if bool(self.ids) == self.delete_all:
            raise ValueError("Choose selected log identifiers or a filtered deletion, but not both")
        if self.delete_all and self.before_id is None:
            raise ValueError("Filtered deletion requires a snapshot boundary")
        return self
