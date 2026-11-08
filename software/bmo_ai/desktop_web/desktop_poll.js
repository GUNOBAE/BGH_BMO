/* One page-owned poll loop. Termination restores the existing shell watchdog. */
(()=>{'use strict';
let stopped=false,hidden=false,timer=null,pending=null,awaitingAck=null,serial=0;
async function poll(){
 if(stopped||pending||awaitingAck!==null)return;
 const controller=new AbortController();pending=controller;
 const timeout=setTimeout(()=>controller.abort(),1200);
 try{
  const response=await fetch('/api/desktop',{cache:'no-store',signal:controller.signal});
  if(!response.ok)throw Error();
  const value=await response.json();
  if(!stopped){awaitingAck=++serial;postMessage({kind:'snapshot',serial:awaitingAck,value});}
 }catch(_){if(!stopped)postMessage({kind:'error'});}
 finally{
  clearTimeout(timeout);pending=null;
  if(!stopped&&awaitingAck===null)timer=setTimeout(poll,hidden?1000:100);
 }
}
onmessage=event=>{
 const message=event.data;
 if(message?.action==='stop'){stopped=true;clearTimeout(timer);pending?.abort();close();return;}
 if(message?.action==='ack'&&message.serial===awaitingAck){awaitingAck=null;clearTimeout(timer);timer=setTimeout(poll,hidden?1000:100);return;}
 if(message?.action==='visibility')hidden=message.hidden===true;
 if(message?.action==='start'||message?.action==='poll'||message?.action==='visibility'){
  clearTimeout(timer);poll();
 }
};
})();
