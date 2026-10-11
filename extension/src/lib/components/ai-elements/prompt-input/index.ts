// Trimmed copy of Svelte AI Elements `prompt-input`: the form, textarea, toolbar and submit button.
// Attachments, the action menu and the `ai` SDK types are left out (the rail has no file input).
import PromptInput from "./prompt-input.svelte";
import PromptInputBody from "./body.svelte";
import PromptInputTextarea from "./textarea.svelte";
import PromptInputToolbar from "./toolbar.svelte";
import PromptInputTools from "./tools.svelte";
import PromptInputSubmit from "./submit.svelte";

export type { ChatStatus } from "./types.js";

export {
	PromptInput,
	PromptInputBody,
	PromptInputTextarea,
	PromptInputToolbar,
	PromptInputTools,
	PromptInputSubmit,
	//
	PromptInput as Root,
	PromptInputBody as Body,
	PromptInputTextarea as Textarea,
	PromptInputToolbar as Toolbar,
	PromptInputTools as Tools,
	PromptInputSubmit as Submit,
};
