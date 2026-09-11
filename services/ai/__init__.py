"""AI: the gateway, and the six workloads that use it.

Nothing outside this package talks to a model provider. Nothing inside it holds
interview state — the workloads are stateless functions over what they are
given, because a model that remembers the interview is a model that is running
the interview.
"""
from .gateway import AIError, AIModelGateway, Generation, Workload, get_gateway, workload_config  # noqa: F401
from .brain import LLMError, RuntimeBrain, get_llm  # noqa: F401
