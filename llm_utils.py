from typing import List

from langchain.chains.query_constructor.schema import AttributeInfo
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI

from cfg import IngestionConfig


class MetadataFieldInfo(BaseModel):
    fields: List[AttributeInfo] = Field(description="List of attribute information")


def query_llm(prompt: str, cfg: IngestionConfig) -> str:
    """
    Query the LLM with a given prompt and return the response.
    """
    match cfg.llm_provider:
        case "openai":
            llm = ChatOpenAI(model_name="gpt-4o", temperature=0)
        case _:
            raise ValueError(f"Unsupported LLM provider: {cfg.llm_provider}")

    structured_llm = llm.with_structured_output(MetadataFieldInfo)

    response = structured_llm.invoke(prompt)

    return response.model_dump_json()
