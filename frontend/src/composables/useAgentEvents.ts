import { shallowRef, type Ref } from 'vue';
import {
  Message,
  MessageContent,
  ToolContent,
  StepContent,
  AttachmentsContent,
} from '../types/message';
import {
  StepEventData,
  ToolEventData,
  MessageEventData,
  ErrorEventData,
  TitleEventData,
  PlanEventData,
  AgentEvent,
} from '../types/event';

export interface AgentEventState {
  messages: Ref<Message[]>;
  title: Ref<string>;
  plan: Ref<PlanEventData | undefined>;
  lastEventId: Ref<string | undefined>;
  lastTool: Ref<ToolContent | undefined>;
  lastNoMessageTool: Ref<ToolContent | undefined>;
}

export interface AgentEventOptions {
  /** Called when a non-message tool is created or updated, so the page can surface it (e.g. in the tool panel). */
  onToolActivity?: (tool: ToolContent) => void;
  /** Fired when stream shows an error assistant bubble or step failed — page maps to phase. */
  onStreamError?: () => void;
}

/**
 * Shared conversion of agent stream events into the UI message list.
 * Used by both ChatPage (live chat) and SharePage (replay).
 */
export function useAgentEvents(state: AgentEventState, options: AgentEventOptions = {}) {
  // Replay into detached shallow refs, then publish the complete history once.
  let activeState = state;

  const getLastStep = (): StepContent | undefined => {
    for (let index = activeState.messages.value.length - 1; index >= 0; index--) {
      const message = activeState.messages.value[index];
      if (message.type === 'step') return message.content as StepContent;
    }
    return undefined;
  };

  const handleMessageEvent = (messageData: MessageEventData) => {
    // Skip blank assistant bubbles (e.g. empty create_plan.message from LLM)
    const text = (messageData.content ?? '').trim();
    if (messageData.role === 'assistant' && !text) {
      if (messageData.attachments && messageData.attachments.length > 0) {
        activeState.messages.value.push({
          type: 'attachments',
          content: {
            ...messageData
          } as AttachmentsContent,
        });
      }
      return;
    }

    // User turn: keep attachments on the same ChatQuestion shell (images above bubble).
    if (messageData.role === 'user') {
      activeState.messages.value.push({
        type: 'user',
        content: {
          ...messageData,
          attachments: messageData.attachments?.length ? messageData.attachments : undefined,
        } as MessageContent,
      });
      return;
    }

    activeState.messages.value.push({
      type: messageData.role,
      content: {
        ...messageData
      } as MessageContent,
    });

    if (messageData.attachments && messageData.attachments.length > 0) {
      activeState.messages.value.push({
        type: 'attachments',
        content: {
          ...messageData
        } as AttachmentsContent,
      });
    }
  };

  const handleToolEvent = (toolData: ToolEventData) => {
    const lastStep = getLastStep();
    const toolContent: ToolContent = {
      ...toolData
    };
    const lastTool = activeState.lastTool;
    if (lastTool.value && lastTool.value.tool_call_id === toolContent.tool_call_id) {
      Object.assign(lastTool.value, toolContent);
    } else {
      if (lastStep?.status === 'running') {
        lastStep.tools.push(toolContent);
      } else {
        activeState.messages.value.push({
          type: 'tool',
          content: toolContent,
        });
      }
      lastTool.value = toolContent;
    }
    if (toolContent.name !== 'message') {
      activeState.lastNoMessageTool.value = toolContent;
      if (activeState === state) options.onToolActivity?.(toolContent);
    }
  };

  const handleStepEvent = (stepData: StepEventData) => {
    const lastStep = getLastStep();
    if (stepData.status === 'running') {
      activeState.messages.value.push({
        type: 'step',
        content: {
          ...stepData,
          tools: []
        } as StepContent,
      });
    } else if (stepData.status === 'completed') {
      if (lastStep) {
        lastStep.status = stepData.status;
      }
    } else if (stepData.status === 'failed') {
      if (lastStep) lastStep.status = stepData.status;
      if (activeState === state) options.onStreamError?.();
    }
  };

  const handleErrorEvent = (errorData: ErrorEventData) => {
    if (activeState === state) options.onStreamError?.();
    activeState.messages.value.push({
      type: 'assistant',
      content: {
        content: errorData.error,
        timestamp: errorData.timestamp
      } as MessageContent,
    });
  };

  const handleTitleEvent = (titleData: TitleEventData) => {
    activeState.title.value = titleData.title;
  };

  const handlePlanEvent = (planData: PlanEventData) => {
    activeState.plan.value = planData;
  };

  const handleEvent = (event: AgentEvent) => {
    // Control / live computer-panel events — not part of the chat message list
    if (
      event.event === 'status_update'
      || event.event === 'terminal_update'
      || event.event === 'file_update'
    ) {
      return;
    }
    if (event.event === 'message') {
      handleMessageEvent(event.data as MessageEventData);
    } else if (event.event === 'tool') {
      handleToolEvent(event.data as ToolEventData);
    } else if (event.event === 'step') {
      handleStepEvent(event.data as StepEventData);
    } else if (event.event === 'done') {
      // Loading state is cleared when the stream ends / status_update arrives
    } else if (event.event === 'wait') {
      // TODO: handle wait event
    } else if (event.event === 'error') {
      handleErrorEvent(event.data as ErrorEventData);
    } else if (event.event === 'title') {
      handleTitleEvent(event.data as TitleEventData);
    } else if (event.event === 'plan') {
      handlePlanEvent(event.data as PlanEventData);
    }
    activeState.lastEventId.value = event.data.event_id;
  };

  const replayEvents = (events: AgentEvent[]) => {
    const replayState: AgentEventState = {
      messages: shallowRef<Message[]>([]),
      title: shallowRef(state.title.value),
      plan: shallowRef<PlanEventData>(),
      lastEventId: shallowRef<string>(),
      lastTool: shallowRef<ToolContent>(),
      lastNoMessageTool: shallowRef<ToolContent>(),
    };
    const previousState = activeState;
    activeState = replayState;
    try {
      for (const event of events) {
        handleEvent(event);
      }
    } finally {
      activeState = previousState;
    }

    state.messages.value = replayState.messages.value;
    state.title.value = replayState.title.value;
    state.plan.value = replayState.plan.value;
    state.lastEventId.value = replayState.lastEventId.value;
    state.lastTool.value = replayState.lastTool.value;
    state.lastNoMessageTool.value = replayState.lastNoMessageTool.value;
  };

  return { handleEvent, replayEvents };
}
