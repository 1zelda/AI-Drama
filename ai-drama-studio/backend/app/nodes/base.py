from abc import ABC, abstractmethod
from typing import Dict, Any

class BaseNode(ABC):
    def __init__(self, config: dict):
        self.config = config
        self.node_id = config.get('id', self.__class__.__name__)

    @abstractmethod
    async def execute(self, context: Dict[str, Any]) -> Any:
        pass

class LLMNode(BaseNode):
    def __init__(self, config: dict):
        super().__init__(config)
        self.provider = config.get('provider', 'openai')
        self.model = config.get('model', 'gpt-4o')
        self.temperature = config.get('temperature', 0.7)
        self.max_tokens = config.get('max_tokens', 2000)
        self.prompt_template = config.get('prompt_template', '')

    async def execute(self, context: Dict[str, Any]) -> Any:
        prompt = self._render_prompt(context)
        return await self._call_llm(prompt)

    def _render_prompt(self, context: Dict[str, Any]) -> str:
        prompt = self.prompt_template
        for key, value in context.items():
            prompt = prompt.replace('{{' + key + '}}', str(value))
        return prompt

    async def _call_llm(self, prompt: str) -> Any:
        return {'prompt': prompt, 'result': 'placeholder'}

