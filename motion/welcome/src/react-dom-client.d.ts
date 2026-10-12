// Narrow declaration for the existing JS-only React DOM 19.2.3 installation.
// Only the documented APIs used by this isolated mount are exposed here.
declare module 'react-dom/client' {
  import type {ReactNode} from 'react';
  export interface Root {render(children:ReactNode):void;unmount():void;}
  export interface RootOptions {identifierPrefix?:string;onUncaughtError?:(error:unknown,info:{componentStack?:string})=>void;}
  export function createRoot(container:Element|DocumentFragment,options?:RootOptions):Root;
}
