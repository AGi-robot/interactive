import json
import unittest
from contextlib import contextmanager
from unittest.mock import patch
from agi_interactive.conversation import ConversationEngine


class Memory:
    def __init__(self):
        self.exchanges = []
        self.events = []
    def record_event(self, text, **kwargs):
        self.events.append({'text': text, **kwargs})
    def recall_events(self, query='', **kwargs):
        return self.events[-4:]
    def recent(self):
        return []
    def add_exchange(self, *exchange):
        self.exchanges.append(exchange)


class StreamingLightingTests(unittest.TestCase):
    def engine(self, rounds):
        requests, events, memory = [], [], Memory()
        @contextmanager
        def stream(*args, **kwargs):
            requests.append(kwargs['json'])
            class Response:
                status_code = 200
                def raise_for_status(self):
                    pass
                def iter_lines(self):
                    for delta, finish in rounds[len(requests) - 1]:
                        yield 'data: ' + json.dumps({'choices': [{'delta': delta, 'finish_reason': finish}]})
                    yield 'data: [DONE]'
            yield Response()
        engine = ConversationEngine(memory=memory, publish=events.append, base_url='http://llm',
                                    model='test', system_prompt='Grace', stream=stream, lighting_url='http://console')
        return engine, requests, events, memory

    def test_fragmented_calls_execute_then_resume_stream(self):
        rounds = [
            [({'tool_calls': [{'index': 0, 'id': 'call1', 'function': {'name': 'set_lighting', 'arguments': '{"bright'}}]}, None),
             ({'tool_calls': [{'index': 0, 'function': {'arguments': 'ness":0.3,"reason":"Soft light for this conversation"}'}}]}, 'tool_calls')],
            [({'content': 'Here you go.'}, 'stop')],
        ]
        engine, requests, events, memory = self.engine(rounds)
        with patch('agi_toolbox.lighting.LightingClient.set_effect', return_value={'connected': True}) as execute:
            # Rebind because the engine captured its method during construction.
            desc, schema, _ = engine.lighting_tools['set_lighting']
            engine.lighting_tools['set_lighting'] = (desc, schema, execute)
            engine.answer('A little colour please')
            execute.assert_called_once_with(brightness=.3, reason="Soft light for this conversation")
        self.assertIn("Soft light", memory.events[0]["text"])
        self.assertEqual(requests[0]['tool_choice'], 'auto')
        self.assertEqual(requests[1]['messages'][-1]['role'], 'tool')
        self.assertEqual(events[-1]['type'], 'done')
        self.assertEqual(memory.exchanges, [('A little colour please', 'Here you go.')])

    def test_incomplete_calls_do_not_execute(self):
        engine, _, events, memory = self.engine([[({'tool_calls': [
            {'index': 0, 'id': 'x', 'function': {'name': 'set_lighting', 'arguments': '{}'}}]}, 'length')]])
        with patch('agi_toolbox.lighting.LightingClient.set_effect') as execute:
            engine.answer('hello')
            execute.assert_not_called()
        self.assertEqual(events[-1]['type'], 'error')
        self.assertEqual(memory.exchanges, [])

    def test_plain_answer_can_decline_tools(self):
        engine, _, events, memory = self.engine([[({'content': 'Hello!'}, 'stop')]])
        engine.answer('hello')
        self.assertEqual(events[-1]['type'], 'done')
        self.assertEqual(memory.exchanges[0][1], 'Hello!')

    def test_action_is_remembered_even_if_final_reply_fails(self):
        engine, _, events, memory = self.engine([
            [({'tool_calls': [{'index': 0, 'id': 'a', 'function': {'name': 'stop_lighting', 'arguments': '{"reason":"Back to our usual colours"}'}}]}, 'tool_calls')],
            [({'content': 'partial'}, 'length')],
        ])
        engine.lighting_tools['stop_lighting'] = ('stop', {}, lambda **kwargs: {'active': False})
        engine.answer('hello')
        self.assertEqual(events[-1]['type'], 'error')
        self.assertEqual(memory.exchanges, [])
        self.assertIn('Back to our usual colours', memory.events[0]['text'])

    def test_regular_memory_recall_after_restart(self):
        import tempfile
        from pathlib import Path
        try:
            from memory.conversation import ConversationStore
        except ImportError:
            self.skipTest('Install Memory to run the cross-package persistence check')
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'conversation.db'
            engine, _, _, _ = self.engine([
                [({'tool_calls': [{'index': 0, 'id': 'a', 'function': {'name': 'set_lighting', 'arguments': '{"preset":"default","reason":"I want our usual colours again"}'}}]}, 'tool_calls')],
                [({'content': 'Back to the usual colours.'}, 'stop')],
            ])
            engine.memory = ConversationStore(path)
            engine.lighting_tools['set_lighting'] = ('set', {}, lambda **kwargs: {'selected': 'default'})
            engine.answer('hello')
            other, requests, _, _ = self.engine([[({'content': 'I chose our usual colours.'}, 'stop')]])
            other.memory = ConversationStore(path)
            other.answer('Why did you choose those colours?')
            self.assertIn('I want our usual colours again', requests[0]['messages'][1]['content'])
