import { useState } from 'react';
import { Bubble, Sender, ThoughtChain, XProvider } from '@ant-design/x';
import english from '@ant-design/x/es/locale/en_US';
import XMarkdown from '@ant-design/x-markdown';
import { Alert, Button, Checkbox, Flex, Pagination, Typography } from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { request } from './api';

type Tool = { id: string; name: string; input?: unknown; output?: string; status: 'running' | 'complete' | 'error' };
type Prompt = { prompt_id: string; kind: 'clarify' | 'approval'; question?: string; description?: string; command?: string; choices?: string[]; multi_select?: boolean };
type Message = { id: number; role: 'user' | 'agent'; content: string; status: 'running' | 'complete' | 'error'; error?: string; events: Tool[]; prompt?: Prompt };
type Conversation = { messages: Message[]; total: number; active: boolean };

const approvalLabels: Record<string, string> = {
  once: 'Allow once', session: 'Allow for this conversation', always: 'Always allow this action', deny: 'Deny',
};

function Question({ message, respond, pending }: { message: Message; respond: (body: unknown) => void; pending: boolean }) {
  const [answer, setAnswer] = useState('');
  const [choices, setChoices] = useState<string[]>([]);
  const prompt = message.prompt!;
  const submit = (text: string) => respond({ message_id: message.id, prompt_id: prompt.prompt_id, answer: text });
  return <Flex vertical gap="small">
    <Alert type={prompt.kind === 'approval' ? 'warning' : 'info'} title={prompt.question || prompt.description || 'Your answer is needed'} />
    {prompt.command && <Typography.Paragraph code>{prompt.command}</Typography.Paragraph>}
    {prompt.multi_select && <><Checkbox.Group options={prompt.choices} value={choices} onChange={setChoices} disabled={pending} />
      <Button onClick={() => submit(choices.join(', '))} disabled={!choices.length || pending}>Send selections</Button></>}
    {!prompt.multi_select && !!prompt.choices?.length && <Flex gap="small" wrap>{prompt.choices.map(choice =>
      <Button key={choice} disabled={pending} onClick={() => submit(choice)}>{prompt.kind === 'approval' ? approvalLabels[choice] ?? choice : choice}</Button>)}</Flex>}
    {prompt.kind === 'clarify' && <Sender aria-label="Answer Hermes" value={answer} onChange={setAnswer} disabled={pending}
      onSubmit={() => submit(answer)} placeholder="Or write your answer" />}
  </Flex>;
}

export default function NotesChat() {
  const client = useQueryClient();
  const [page, setPage] = useState(1);
  const [text, setText] = useState('');
  const conversation = useQuery({ queryKey: ['chat', page], queryFn: () => request<Conversation>(`/api/chat?page=${page}`),
    refetchInterval: query => query.state.data?.active ? 500 : false, refetchOnWindowFocus: true, retry: false });
  const send = useMutation({ mutationFn: (body: unknown) => request('/api/chat', body), onSuccess: async () => {
    setText(''); setPage(1); await client.invalidateQueries({ queryKey: ['chat'] });
  } });
  const control = useMutation({ mutationFn: (body: unknown) => request('/api/chat/control', body),
    onSuccess: () => client.invalidateQueries({ queryKey: ['chat'] }) });
  const messages = conversation.data?.messages ?? [];
  const active = messages.find(message => message.status === 'running');
  const error = send.error || control.error || conversation.error;
  return <XProvider locale={english} theme={{ token: { colorPrimary: '#0f68ea', fontFamily: 'Inter Variable, sans-serif', borderRadius: 12 } }}>
    <Flex vertical gap="large" style={{ maxWidth: 900, margin: 'auto', background: 'white', padding: 24, borderRadius: 20 }}>
      <Typography.Title level={2} style={{ margin: 0, fontSize: 22 }}>Talk with Hermes</Typography.Title>
      {!messages.length && <Flex gap="small" wrap>{['Help me refine my apartment preferences.', 'What do my favorite listings have in common?'].map(suggestion =>
        <Button key={suggestion} onClick={() => setText(suggestion)} style={{ whiteSpace: 'normal', height: 'auto' }}>{suggestion}</Button>)}</Flex>}
      {error && <Alert type="error" title="Conversation error" description={error.message} />}
      <Bubble.List aria-label="Conversation with Hermes" autoScroll styles={{ scroll: { maxHeight: '55vh', minHeight: 180, overflowY: 'auto' } }}
        items={messages.map(message => ({ key: message.id, role: message.role === 'agent' ? 'ai' : 'user',
          content: message.content, header: message.role === 'agent' ? 'Hermes' : 'You', streaming: message.status === 'running',
          loading: message.status === 'running' && !message.content && !message.events.length,
          contentRender: (content: string) => <XMarkdown content={content} streaming={{ hasNextChunk: message.status === 'running' }} />,
          footer: <Flex vertical gap="small">
            {!!message.events.length && <ThoughtChain items={message.events.map(tool => ({ key: tool.id, title: tool.name, collapsible: true,
              status: tool.status === 'running' ? 'loading' : tool.status === 'error' ? 'error' : 'success',
              content: <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{tool.output || JSON.stringify(tool.input, null, 2)}</Typography.Paragraph>,
            }))} />}
            {message.error && <Alert type="error" title="Hermes could not finish" description={message.error} />}
            {message.prompt && <Question message={message} respond={control.mutate} pending={control.isPending} />}
          </Flex>,
        }))} role={{ ai: { placement: 'start', variant: 'borderless' }, user: { placement: 'end', variant: 'filled' } }} />
      {(conversation.data?.total ?? 0) > 20 && <Pagination simple pageSize={20} total={conversation.data?.total} current={page} onChange={setPage} />}
      {page > 1 && <Button onClick={() => setPage(1)}>Latest messages</Button>}
      <Sender aria-label="Message Hermes" value={text} onChange={setText} loading={!!conversation.data?.active || send.isPending}
        readOnly={!!conversation.data?.active} placeholder="Tell Hermes what you are looking for..." autoSize={{ minRows: 2, maxRows: 6 }}
        onSubmit={() => { if (text.trim()) send.mutate({ text, request_id: crypto.randomUUID() }); }}
        onCancel={() => { if (active) control.mutate({ message_id: active.id, interrupt: true }); }}
        suffix={(_, { components }) => conversation.data?.active ? <components.LoadingButton aria-label="Stop reply" /> : <components.SendButton aria-label="Send message" />} />
    </Flex>
  </XProvider>;
}
