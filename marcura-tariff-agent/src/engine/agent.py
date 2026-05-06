# agent.py
# TODO: Build and expose the LangChain agentic pipeline powered by Gemini 1.5 Pro.
# Responsibilities:
#   - Initialise ChatGoogleGenerativeAI with the configured API key
#   - Compose the ReAct / tool-calling agent with retrieval and calc tools
#   - Define the system prompt that guides tariff reasoning
#   - Expose a run(query: str, context: dict) -> TariffResult interface
