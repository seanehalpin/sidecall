// Trimmed copy of Svelte AI Elements `message`: only the core bubble parts. Branching, attachments and
// the streamdown-based response renderer are left out (they pull in shiki/streamdown/dialogs).
import Message from "./message.svelte";
import MessageContent from "./message-content.svelte";

export type { MessageRole } from "./types";
export { Message, MessageContent, Message as Root, MessageContent as Content };
