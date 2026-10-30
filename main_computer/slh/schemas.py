"""Versioned first-class request schemas for SLH Phase 2A. Not wire executors."""
S = {'type': 'string', 'maxLength': 2048}

def object_schema(required=(), fields=None):
    return {'type': 'object', 'required': list(required),
            'properties': fields or {}, 'additionalProperties': False}

AUTH_REQUESTS = {
    'bootstrap_begin': object_schema(('org_id','bootstrap_secret'),
                    {'org_id':S,'bootstrap_secret':S}),
    'bootstrap_stage_admin': object_schema(
                    ('admin_id','admin_name','device_id','public_key','proof','bootstrap_secret'),
                    {k:S for k in ('admin_id','admin_name','device_id','public_key','proof','bootstrap_secret')}),
    'bootstrap_cancel': object_schema(('bootstrap_secret',),{'bootstrap_secret':S}),
    'bootstrap_activate_p256': object_schema(('bootstrap_secret','defer_totp'),{'bootstrap_secret':S,'defer_totp':{'type':'boolean'}}),
    'bootstrap_admin': object_schema(
        ('org_id','admin_id','admin_name','device_id','public_key','bootstrap_secret','proof'),
        {k:S for k in ('org_id','admin_id','admin_name','device_id','public_key','bootstrap_secret','proof')}),
    'request_enrollment': object_schema(
        ('selected_admin','device_id','public_key','kind','device_name','claimed_user','requested_role','client_nonce','proof'),
        {k:S for k in ('selected_admin','device_id','public_key','kind','device_name','claimed_user','requested_role','client_nonce','proof')}),
    'approve_enrollment': object_schema(('request_id',),{'request_id':S,'enrollment_key':S}),
    'challenge': object_schema(('device_id',),{'device_id':S}),
    'verify': object_schema(('device_id','challenge_id','signature'),
                            {'device_id':S,'challenge_id':S,'signature':S}),
    'edit_device': object_schema((),{'name':S,'notes':{'type':'string','maxLength':2000}}),
    'issue_key': object_schema((),{'ttl_seconds':{'type':'integer','minimum':1,'maximum':600}}),
    'change_policy': object_schema(('physical_inspection_required',),
                                    {'physical_inspection_required':{'type':'boolean'}}),
}

KNOWN_RESPONSES={
    'bootstrap_begin': object_schema(('transaction_id','state','org_id','created_at','expires_at','proof_context')) ,
    'bootstrap_stage_admin': object_schema(('transaction_id','state','org_id','admin_id','device_id','fingerprint','expires_at')) ,
    'bootstrap_transaction_status': object_schema(('transaction_id','state','org_id','created_at','expires_at')) ,
    'bootstrap_cancel': object_schema(('transaction_id','state')) ,
    'bootstrap_status': object_schema(('initialized','bootstrap'),
                                      {'initialized':{'type':'boolean'},'bootstrap':S,'organization_id':S}),
    'approve_enrollment': object_schema(('status','request_id','device_id','user_id','authorized_by'),
                                      {k:S for k in ('status','request_id','device_id','user_id','authorized_by')}),
    'challenge': object_schema(('version','purpose','org_id','audience','device_id','challenge_id','nonce','issued_at','expires_at'),
                 {**{k:S for k in ('purpose','org_id','audience','device_id','challenge_id','nonce')},
                   'version':{'type':'integer'},'issued_at':{'type':'integer'},'expires_at':{'type':'integer'}}),
    'verify': object_schema(('access_token','token_type','expires_at','device_id','role'),
                {**{k:S for k in ('access_token','token_type','device_id','role')},'expires_at':{'type':'integer'}}),
}
