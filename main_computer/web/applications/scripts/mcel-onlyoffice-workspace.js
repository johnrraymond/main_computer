/* Compose the external editor and file picker into MCEL-authored regions. */
(function(global){'use strict';
  function mount(root,importWorkbook){
    const region=name=>root.querySelector('[data-mcel-region="interface-region:onlyoffice.'+name+'"]');
    const header=region('header'),library=region('library'),editor=region('editor'),advanced=region('advanced'),activity=region('activity');
    if(!header||!library||!editor||!advanced||!activity)throw Error('ONLYOFFICE regions unavailable');
    const layout=root.firstElementChild;if(!layout)throw Error('MCEL layout unavailable');layout.classList.add('mcel-onlyoffice-layout');
    // Preserve the shared hint-driven layout and its scroll owners. Only mount
    // the external editor and supporting controls into their authored regions.
    const host=document.createElement('div');host.id='onlyoffice-editor-host';host.className='mcel-onlyoffice-editor-host';host.setAttribute('aria-label','Isolated spreadsheet editor');editor.append(host);
    const hint=document.createElement('p');hint.textContent='Click a workbook to open it.';host.append(hint);
    const picker=document.createElement('input');picker.type='file';picker.id='onlyoffice-upload-file';picker.accept='.xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';picker.setAttribute('aria-label','Choose XLSX workbook to upload');
    picker.hidden=true;
    const importButton=document.createElement('button');importButton.type='button';importButton.textContent='Import';
    header.append(importButton,picker);
    importButton.addEventListener('click',()=>{picker.value='';picker.click();});
    const notice=document.createElement('output');notice.id='onlyoffice-status';notice.setAttribute('aria-live','polite');activity.prepend(notice);
    host.addEventListener('mcel:onlyoffice-error',event=>notice.textContent=String(event.detail));
    picker.addEventListener('change',async()=>{
      if(!picker.files.length||importButton.disabled)return;
      importButton.disabled=true;
      try{const receipt=await importWorkbook();if(receipt.disposition!=='completed')notice.textContent='Import failed: '+String(receipt.reason||receipt.disposition);}
      catch(error){notice.textContent='Import failed: '+String(error.message||error);}
      finally{picker.value='';importButton.disabled=false;}
    });
    const toggle=document.createElement('button');toggle.type='button';toggle.textContent='Advanced';toggle.setAttribute('aria-expanded','false');header.append(toggle);advanced.hidden=true;
    let chat=null;const chatHost=document.createElement('aside');chatHost.id='onlyoffice-advanced-chat-panel';chatHost.className='mcel-onlyoffice-chat';advanced.append(chatHost);
    toggle.addEventListener('click',()=>{advanced.hidden=!advanced.hidden;toggle.setAttribute('aria-expanded',String(!advanced.hidden));if(!advanced.hidden&&!chat){
      try{chat=global.MainComputerChatConsole?.mountEmbedded(chatHost,{embedId:'onlyoffice',activeApp:'onlyoffice',idPrefix:'onlyoffice-advanced-chat',classPrefix:'onlyoffice-advanced',title:'Advanced Chat Console',subtitle:'Chat and notebook tools alongside the active workbook.',notebookId:'onlyoffice-advanced-chat-notebook',statusId:'onlyoffice-advanced-chat-status',threadTitle:'ONLYOFFICE Advanced Chat',initialStatus:'advanced chat ready',status:message=>{if(message)notice.textContent=message;}});if(!chat)throw Error('Shared Chat Console unavailable');}
      catch(error){chatHost.textContent='Advanced Chat Console unavailable: '+String(error.message||error);}}
    });
    const name=library.querySelector('[data-mcel-node="surface-node:onlyoffice.new-workbook"] input');if(!name)throw Error('Workbook name input unavailable');
    return {host,picker,notice,name};
  }
  global.McelOnlyOfficeWorkspace=Object.freeze({mount});
})(window);
