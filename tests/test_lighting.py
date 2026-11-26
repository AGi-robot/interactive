import json
import unittest
from contextlib import contextmanager
from unittest.mock import patch
from agi_interactive.conversation import ConversationEngine


class Memory:
    def __init__(self):
        self.exchanges = []
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
             ({'tool_calls': [{'index': 0, 'function': {'arguments': 'ness":0.3}'}}]}, 'tool_calls')],
            [({'content': 'Here you go.'}, 'stop')],
        ]
        engine, requests, events, memory = self.engine(rounds)
        with patch('agi_toolbox.lighting.LightingClient.set_effect', return_value={'connected': True}) as execute:
            # Rebind because the engine captured its method during construction.
            desc, schema, _ = engine.lighting_tools['set_lighting']
            engine.lighting_tools['set_lighting'] = (desc, schema, execute)
            engine.answer('A little colour please')
            execute.assert_called_once_with(brightness=.3)
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
