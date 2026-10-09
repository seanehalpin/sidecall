import { mount } from 'svelte';
import '@/lib/ui/theme.css';
import Options from './Options.svelte';

const target = document.getElementById('app');
if (!target) throw new Error('missing #app');
mount(Options, { target });
