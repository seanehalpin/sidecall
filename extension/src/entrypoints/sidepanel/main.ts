import { mount } from 'svelte';
import '@/lib/ui/theme.css';
import App from './App.svelte';

const target = document.getElementById('app');
if (!target) throw new Error('missing #app');
mount(App, { target });
