import json
from unittest.mock import Mock
import pytest
from agi_interactive import ConversationEngine


@pytest.mark.parametrize('terminal', [None, 'done', 'finish_reason'])
def test_only_complete_streams_are_saved(terminal):
    events = []
    memory = Mock()
    memory.recent.return_value = []
    chunk = lambda delta, **extra: 'data: ' + json.dumps({'choices': [{'delta': delta, **extra}]})
    lines = [chunk({'content': 'Hello'})]
    if terminal == 'done': lines.append('data: [DONE]')
    if terminal == 'finish_reason': lines.append(chunk({}, finish_reason='stop'))
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.iter_lines.return_value = lines
    engine = ConversationEngine(memory=memory, publish=events.append, base_url='http://unused',
                                model='fixture', system_prompt='AIVA', stream=Mock(return_value=response))
    engine.answer('hello', 'request-1')
    if terminal:
        memory.add_exchange.assert_called_once_with('hello', 'Hello')
        assert events[-1]['type'] == 'done'
    else:
        memory.add_exchange.assert_not_called()
        assert events[-1]['type'] == 'error'


def test_length_finish_is_not_saved():
    events = []
    memory = Mock()
    memory.recent.return_value = []
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.iter_lines.return_value = [
        'data: ' + json.dumps({'choices': [{'delta': {'content': 'Partial'}, 'finish_reason': 'length'}]}),
        'data: [DONE]',
    ]
    ConversationEngine(memory=memory, publish=events.append, base_url='http://unused',
                       model='fixture', system_prompt='AIVA', stream=Mock(return_value=response)).answer('hello')
    memory.add_exchange.assert_not_called()
    assert events[-1]['type'] == 'error'
    assert 'incomplete' in events[-1]['content']
