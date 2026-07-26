from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.context import get_module_context, ContextRequest
from app.core.config import settings
from app.services.generation import acompletion, get_generation_model
from app.services.llm_config import resolve_llm_config

router = APIRouter()


@router.get(
    "/generation-provider",
    summary="Get generation provider status",
    description="Returns the resolved LLM generation configuration status.",
)
async def generation_provider_status():
    config = resolve_llm_config()
    return {
        "provider": config.provider,
        "key_configured": bool(config.api_key),
        "base_url": config.base_url,
        "chat_model": config.chat_model,
        "dev_model": config.resolved_dev_model,
        # Embeddings are still configured via .env — they are not part of the
        # switchable provider config.
        "embedding_model": settings.OPENAI_EMBEDDING_MODEL,
    }


class GenerateRequest(BaseModel):
    query: str = Field(..., description="Module or feature query to generate tests for.", examples=["Login screen"])
    project_path: str = Field(..., description="Absolute path to the project root to retrieve context from.", examples=["/Users/leo.e/dev/flutter/my_app"])


class PromptRequest(BaseModel):
    prompt: str = Field(..., description="User prompt text to send directly to the generation model.", examples=["Write 3 positive and 2 negative test ideas for signup flow."])
    system_prompt: str | None = Field(default=None, description="Optional system instruction to control assistant behavior.", examples=["You are a concise QA assistant."])


@router.post(
    "/generate-tests",
    summary="Generate UI test scenarios from code context",
    description=(
        "Runs retrieval-augmented generation: fetches relevant code context, builds a prompt, "
        "and returns JSON scenarios for vision-based test execution."
    ),
)
async def generate_module_tests(request: GenerateRequest):
    """
    End-to-end RAG flow:
    1. Retrieval: Get related files and dependencies.
    2. Augmentation: Build a prompt with the retrieved code.
    3. Generation: Call LLM to generate test cases.
    """
    # 1. Retrieval (R)
    context_request = ContextRequest(query=request.query, project_path=request.project_path)
    context_res = await get_module_context(context_request)

    files = context_res.get("context", [])
    if not files:
        raise HTTPException(status_code=404, detail="No relevant code found for the given module query.")

    # 2. Augmentation (A)
    context_str = ""
    for f in files:
        context_str += f"\n--- FILE: {f['file_path']} ---\n"
        context_str += f['content']
        context_str += "\n---------------------------\n"

    system_prompt = """
    You are an expert Mobile UI Testing Architect.
    Your task is to generate precise, step-by-step testing instructions for a Vision-based Execution Agent.
    
    You MUST return the output as a JSON object with a "scenarios" key containing a list of test scenarios.
    Each scenario object MUST have:
    - "scenario_name": String
    - "flow_type": "Positive" or "Negative"
    - "steps": A list of strings (plain English commands like 'Tap on X', 'Enter text Y')
    - "verification_points": A list of strings (what the agent should verify visually)
    
    Reference UI elements by their Visible Text, Semantic Labels, or Tooltips identified from the code context.
    Do NOT include any Dart code or markdown formatting outside the JSON.
    """

    user_prompt = f"""
    Implementation context for '{request.query}':
    {context_str}

    TASK:
    Analyze the code and generate a JSON-formatted testing plan for '{request.query}'.
    Focus on descriptive steps for a Vision-based AI agent.
    """

    # 3. Generation (G) — LiteLLM
    model_name = get_generation_model()
    print(f"Generating tests using model: {model_name} (Requested: {len(context_str)} chars)")
    try:
        response = await acompletion(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
        )

        import json
        generated_json = json.loads(response.choices[0].message.content)

        return {
            "module": request.query,
            "files_analyzed": [f["file_path"] for f in files],
            "scenarios": generated_json.get("scenarios", []),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM Generation Error: {str(e)}")


@router.post(
    "/generate-raw",
    summary="Generate raw response for prompt",
    description="Sends prompt directly to OpenAI and returns raw model text.",
)
async def generate_raw_response(request: PromptRequest):
    model_name = get_generation_model()

    messages = []
    if request.system_prompt:
        messages.append({"role": "system", "content": request.system_prompt})
    messages.append({"role": "user", "content": request.prompt})

    try:
        response = await acompletion(
            model=model_name,
            messages=messages,
        )
        return {
            "provider": "openai",
            "model": model_name,
            "response": response.choices[0].message.content,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Raw LLM Generation Error: {str(e)}")
