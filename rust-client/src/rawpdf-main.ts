import { mount } from 'svelte'
import './app.css'
import RawPdf from './RawPdf.svelte'

export default mount(RawPdf, { target: document.getElementById('app')! })
