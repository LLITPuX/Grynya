---
description: Архітектура LLM провайдерів для Hybrid Cognitive Pipeline
---

# Provider Architecture Rule

## ProviderResponse (типізований об'єкт)

Всі провайдери ПОВИННІ повертати `ProviderResponse`, не `str`:

```python
@dataclass
class ProviderResponse:
    content: str        # Текст відповіді
    token_usage: int    # Витрачені токени
    model_name: str     # Яка модель відповіла
```

## LLMProvider Interface

```python
class LLMProvider(ABC):
    @abstractmethod
    async def generate_response(
        self, 
        history: List[Dict], 
        system_prompt: str
    ) -> ProviderResponse
    
    @abstractmethod
    def get_provider_name(self) -> str
```

## Провайдери (Ролі)

| Name | Role | Файл |
|------|------|------|
| OllamaProvider | `fast` | `core/providers/ollama_provider.py` |
| GeminiProvider | `primary` | `core/providers/gemini_provider.py` |
| OpenAIProvider | `fallback` | `core/providers/openai_provider.py` |

## Switchboard Logic

```python
try:
    return await primary.generate_response(...)
except RateLimitError:
    log_to_graph("FALLBACK_TRIGGERED")
    return await fallback.generate_response(...)
```

## Observability (КРИТИЧНО!)

Кожен Fallback ПОВИНЕН створювати вузол у графі:

```cypher
CREATE (:SystemEvent {
    type: 'FALLBACK',
    from_provider: 'gemini',
    to_provider: 'openai',
    reason: '429_quota',
    timestamp: datetime()
})
```
