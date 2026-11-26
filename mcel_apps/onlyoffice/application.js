"use strict";
const mcel=require('@mcel/app');
module.exports=mcel.defineApp({id:'onlyoffice',title:'ONLYOFFICE'},dsl=>{
  const text=dsl.field.text();
  const rows=dsl.field.list(dsl.field.record({path:text,size:text}),{key:'path'});
  const files=dsl.state.derived('workbooks',rows);
  const selected=dsl.state.rendererLocal('selected-workbook',text,{initial:''});
  const active=dsl.state.derived('active-workbook',text,{initial:'No workbook open'});
  const status=dsl.state.derived('server-status',text,{initial:'Checking Document Server'});
  const result=dsl.state.derived('operation-result',text,{initial:'Ready'});
  const read=dsl.capability.external('workbook-reader',{risk:'external-read',description:'Authenticated workbook inventory, status and isolated editor configuration'});
  read.operation('list','listWorkbooks',{result:rows});
  read.operation('status','serverStatus',{result:text});
  read.operation('open','openWorkbook',{inputs:{path:text},result:text});
  read.operation('reload','reloadWorkbook',{result:text});
  const write=dsl.capability.external('workbook-writer',{risk:'source-write',description:'Explicit workbook creation, upload and force-save'});
  write.operation('create','createWorkbook',{inputs:{name:text},result:rows});
  write.operation('upload','uploadWorkbook',{result:rows});
  write.operation('save','forceSave',{result:text});
  const comment=dsl.capability.external('workbook-comment',{risk:'consequential',description:'Attach code as a comment, never execute it in Main Computer'});
  const language=text.oneOf(['javascript','python','basic']);
  comment.operation('attach','attachComment',{inputs:{language,code:text},result:text});
  const refresh=dsl.intent.capabilityRequest('refresh-workbooks',read,{operation:'list',resultTo:files});
  const probe=dsl.intent.capabilityRequest('refresh-server-status',read,{operation:'status',resultTo:status});
  const choose=dsl.intent.selectFromCollection('select-workbook',files,selected);
  const open=dsl.intent.capabilityRequest('open-workbook',read,{operation:'open',arguments:{path:selected},resultTo:active});
  const reload=dsl.intent.capabilityRequest('reload-editor',read,{operation:'reload',resultTo:active});
  const create=dsl.intent.capabilityRequest('create-workbook',write,{operation:'create',inputs:{name:text},resultTo:files,confirmationRequired:false});
  const upload=dsl.intent.capabilityRequest('upload-workbook',write,{operation:'upload',resultTo:files,confirmationRequired:false});
  const save=dsl.intent.capabilityRequest('save-now',write,{operation:'save',resultTo:result,confirmationRequired:false});
  const attach=dsl.intent.capabilityRequest('attach-code',comment,{operation:'attach',inputs:{language,code:text},resultTo:result,confirmationRequired:false});
  const surface=dsl.surface.bindHtml('workspace',{bindings:{
    header:[],
    library:[dsl.surface.action('refresh-workbooks',{intent:refresh,label:'Refresh'}),dsl.surface.collection('workbooks',{key:'path',fields:['path','size'],state:files,select:choose,clearable:false,label:'XLSX workbooks'}),dsl.surface.form('new-workbook',{fields:{name:text},defaults:{name:''},placeholders:{name:'Automatic workbook name'},resetControl:false,intent:create,submitLabel:'New XLSX'})],
    editor:[],
    advanced:[dsl.surface.form('selected-cell-code',{fields:{language,code:text},defaults:{language:'javascript',code:''},intent:attach,submitLabel:'Attach code to selected cells'})],
    activity:[dsl.surface.text('active-workbook',{value:active}),dsl.surface.text('operation-result',{value:result}),dsl.surface.receipt('receipts')]
  }});
  return {states:[files,selected,active,status,result],capabilities:[read,write,comment],intents:[refresh,probe,choose,open,reload,create,upload,save,attach],surfaces:[surface]};
});
