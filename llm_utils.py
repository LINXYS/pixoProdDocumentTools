from typing import List

from langchain.chains.query_constructor.schema import AttributeInfo
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI


class MetadataFieldInfo(BaseModel):
    fields: List[AttributeInfo] = Field(description="List of attribute information")


def query_llm(prompt: str) -> str:
    """
    Query the LLM with a given prompt and return the response.
    """
    llm = ChatOpenAI(model_name="gpt-4o", temperature=0)
    
    structured_llm = llm.with_structured_output(MetadataFieldInfo)

    response = structured_llm.invoke(prompt)

    return response.model_dump_json()
