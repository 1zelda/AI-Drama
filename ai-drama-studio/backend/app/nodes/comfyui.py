import asyncio
from typing import Dict, Any
import json
import os

try:
    from comfy_sdk import Comfy
    HAS_COMFY = True
except ImportError:
    HAS_COMFY = False
    Comfy = None

class ComfyUINode:
    def __init__(self, config: dict):
        self.config = config
        self.workflow_path = config.get('workflow_path', 'workflows/default.json')
        self.comfy_url = config.get('comfy_url', 'http://localhost:8188')
        self.api_key = config.get('api_key', None)
        self.client = None
        if HAS_COMFY:
            if self.api_key:
                self.client = Comfy(api_key=self.api_key)
            else:
                self.client = Comfy()

    async def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        if not self.client:
            return {'error': 'ComfyUI SDK not available', 'context': context}
        try:
            if os.path.exists(self.workflow_path):
                wf = self.client.workflows.from_file(self.workflow_path)
            else:
                wf = self.client.workflows.from_json(self._default_workflow())
            for node_id, inputs in context.get('inputs', {}).items():
                for input_name, value in inputs.items():
                    wf.set_input(node_id, input_name, value)
            job = self.client.run(wf)
            outputs = {}
            for node_id in context.get('output_nodes', []):
                results = job.get_outputs(node_id)
                outputs[node_id] = [r.to_file(r.name) if hasattr(r, 'to_file') else str(r) for r in results]
            return {'status': 'completed', 'outputs': outputs, 'job_id': job.id}
        except Exception as e:
            return {'error': str(e), 'context': context}

    def _default_workflow(self) -> dict:
        return {'3': {'class_type': 'KSampler', 'inputs': {'seed': 0, 'steps': 20}}, '9': {'class_type': 'SaveImage', 'inputs': {'images': ['8', 0]}}}

    def get_status(self) -> dict:
        return {'available': HAS_COMFY, 'workflow_path': self.workflow_path, 'comfy_url': self.comfy_url}

