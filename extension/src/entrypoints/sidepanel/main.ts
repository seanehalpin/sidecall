import { mount } from 'svelte';
import '@/lib/ui/theme.css';
import { initColorScheme } from '@/lib/ui/theme';
import App from './App.svelte';

initColorScheme();
const target = document.getElementById('app');
if (!target) throw new Error('missing #app');
mount(App, { target });
