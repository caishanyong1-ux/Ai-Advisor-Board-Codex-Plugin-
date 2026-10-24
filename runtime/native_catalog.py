"""Reviewed transport metadata, not account entitlement or a live ranking."""
from datetime import datetime, timezone
REVIEWED = datetime(2026, 10, 24, tzinfo=timezone.utc).timestamp()
PRICE_SOURCE = 'https://cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing'
ROOT = 'https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/'

# Explicit coverage reviewed at PRICE_SOURCE on REVIEWED. Model-capacity changes
# must not automatically extend a quote; missing entries retain legacy coverage.
VERIFIED_PRICE_COVERAGE = {'gemini-4-argon': 1048576,
 'gemini-3.8-flash': 1048576,
 'gemini-3.7-flash': 1048576,
 'gemini-3.6-flash': 1048576,
 'gemini-3.5-flash': 1048576,
 'gemini-3.5-flash-lite': 1048576,
 'gemini-3.1-flash-lite': 1048576,
 'gemini-3.1-pro-preview': 1048576,
 'gemini-3.1-pro-preview-customtools': 1048576,
 'gemini-4-argon-cyber-permissive': 1048576,
 'gemini-3.8-flash-cyber': 1048576,
 'claude-fable-5-1': 1048576,
 'claude-fable-5': 1048576,
 'claude-opus-5-5': 1048576,
 'claude-opus-5': 1048576,
 'claude-opus-4-8': 1048576,
 'claude-opus-4-7': 1048576,
 'claude-opus-4-6': 1048576,
 'claude-sonnet-5-5': 1000000,
 'claude-sonnet-5': 1048576,
 'claude-sonnet-4-6': 1048576,
 'claude-haiku-5-5': 1048576,
 'claude-opus-4-5@20251101': 200000,
 'claude-sonnet-4-5@20250929': 200000,
 'claude-haiku-4-5@20251001': 200000,
 'deepseek-ai/deepseek-v4.1-flash-maas': 1048576,
 'zai-org/glm-5.2-maas': 1048576,
 'openai/gpt-oss-120b-maas': 131072,
 'google/gemma-4-26b-a4b-it-maas': 262144,
 'meta/llama-4-scout-17b-16e-instruct-maas': 1310720,
 'meta/llama-4-maverick-17b-128e-instruct-maas': 524288,
 'xai/grok-4.7': 524288,
 'xai/grok-4.6': 524288,
 'xai/grok-4.3': 200000,
 'xai/grok-4.20-reasoning': 2000000,
 'xai/grok-4.20-non-reasoning': 2000000,
 'mistral-medium-3': 128000,
 'mistral-small-2503': 128000,
 'codestral-2': 128000}


def build_models():
    models = {}
    def add(ident, publisher, api, rates, path, efforts=('none',), context=1048576, location='global', **extra):
        quote = None if rates is None else {'input_per_million': rates[0], 'output_per_million': rates[1],
            'checked_at': REVIEWED, 'valid_until': REVIEWED + 7 * 86400, 'source': PRICE_SOURCE,
            'note': 'Full standard list rates; no conditional credits or cache savings assumed.',
            'conditions': 'USD/1M tokens; standard online text, displayed region; full context with listed tier boundaries; all output including reasoning; tools disabled',
            'input_token_limit': min(context, VERIFIED_PRICE_COVERAGE.get(ident, 100000)), 'tiers': []}
        models[ident] = dict(id=ident, name=ident, publisher=publisher, api_family=api,
            execution_provider='vertex', wire_model=ident, location=location, launch_stage='GA',
            max_input_tokens=context, max_output_tokens=32768, context_tokens=context, efforts=list(efforts),
            capabilities=['text'], model_source=ROOT + path, pricing=quote, inference_verified=False)
        models[ident].update(extra)
        return models[ident]
    for ident, rates, minimal in [
        ('gemini-4-argon',(4,20),False),('gemini-3.8-flash',(1.5,7.5),False),('gemini-3.7-flash',(1.5,7.5),False),
        ('gemini-3.6-flash',(1.5,7.5),True),('gemini-3.5-flash',(1.5,9),True),('gemini-3.5-flash-lite',(.3,2.5),True),
        ('gemini-3.1-flash-lite',(.25,1.5),True),('gemini-3.1-pro-preview',(2,12),False),
        ('gemini-3.1-pro-preview-customtools',(2,12),False),('gemini-3-flash-preview',None,True),
        ('gemini-4-argon-cyber-permissive',(4,20),False),('gemini-3.8-flash-cyber',(1.5,7.5),False)]:
        m=add(ident,'google','gemini',rates,'gemini',(['minimal'] if minimal else [])+['low','medium','high'])
        if 'preview' in ident: m['launch_stage']='Preview'
        if 'cyber' in ident: m['access_note']='Google allowlist required; ordinary credentials do not establish access.'
        if ident=='gemini-3-flash-preview': m['access_note']='Current official output tariff needs verification; historical launch pricing is insufficient.'
        if ident in {'gemini-3.6-flash','gemini-3.7-flash'}:
            m['retirement_at']=datetime.fromisoformat('2026-11-19' if ident=='gemini-3.6-flash' else '2027-01-28').replace(tzinfo=timezone.utc).timestamp()
        if ident in {'gemini-3.8-flash','gemini-3.7-flash','gemini-3.6-flash'}:
            m['pricing']['note']='Full $1.50/$7.50 reserved. Advertised $0.75/$3.75 depends on conditional credits through 2026-12-31.'
    modern=['low','medium','high','xhigh','max']
    for ident,rates,efforts in [
        ('claude-fable-5-1',(10,50),modern),('claude-fable-5',(10,50),modern),
        ('claude-opus-5-5',(4,20),modern),('claude-opus-5',(5,25),modern),('claude-opus-4-8',(5,25),modern),
        ('claude-opus-4-7',(5,25),modern),('claude-opus-4-6',(5,25),['low','medium','high','max']),
        ('claude-sonnet-5-5',(2,10),modern),('claude-sonnet-5',(2,10),modern),
        ('claude-sonnet-4-6',(3,15),['low','medium','high','max']),('claude-haiku-5-5',(.1,.5),modern)]:
        m=add(ident,'anthropic','claude',rates,'partner-models/claude/'+ident.removeprefix('claude-'),efforts,thinking_mode='adaptive')
        if 'fable' in ident: m['access_note']='Additional partner terms include prompt sharing and retention up to 30 days. Review and accept in your own provider account before use.'
        if ident=='claude-haiku-5-5':
            m['pricing']['tiers']=[dict(from_input_tokens=100000, input_per_million=.5, output_per_million=2.5)]
            m['pricing']['note']='At >=100K input, $0.50/$2.50 applies to all input/output tokens.'
    for ident,rates in [('claude-opus-4-5@20251101',(5,25)),('claude-sonnet-4-5@20250929',(3,15)),('claude-haiku-4-5@20251001',(1,5))]:
        add(ident,'anthropic','claude',rates,'partner-models/claude',['none','low','medium','high'],context=200000,thinking_mode='budget',
            effort_note='none disables thinking; low/medium/high freeze 1024/4096/8192 thinking tokens below the total output cap.')
    for ident,pub,rates,context,stage,extra in [
        ('deepseek-ai/deepseek-v4.1-flash-maas','deepseek-ai',(.3,1.2),1048576,'Preview',{}),
        ('zai-org/glm-5.2-maas','zai-org',(1.4,4.4),1048576,'Preview',{'parameters':{'chat_template_kwargs':{'enable_thinking':True}}}),
        ('openai/gpt-oss-120b-maas','openai',(.09,.36),131072,'GA',{'efforts':['none','high']}),
        ('google/gemma-4-26b-a4b-it-maas','google',(.15,.6),262144,'Experimental',{'parameters':{'chat_template_kwargs':{'enable_thinking':False}}}),
        ('meta/llama-4-scout-17b-16e-instruct-maas','meta',(.25,.7),1310720,'GA',{'location':'us-east5','max_output_tokens':8192}),
        ('meta/llama-4-maverick-17b-128e-instruct-maas','meta',(.35,1.15),524288,'GA',{'location':'us-east5','max_output_tokens':8192}),
        ('xai/grok-4.7','xai',(2,6),524288,'Preview',{}),('xai/grok-4.6','xai',(2,6),524288,'GA',{}),
        ('xai/grok-4.3','xai',(1.25,2.5),200000,'Preview',{}),
        ('xai/grok-4.20-reasoning','xai',(1.25,2.5),2000000,'See access note',{}),
        ('xai/grok-4.20-non-reasoning','xai',(1.25,2.5),2000000,'See access note',{})]:
        m=add(ident,pub,'openai_maas',rates,'open-models',context=context)
        m.update(launch_stage=stage,**extra)
        if pub=='xai':
            m.update(budget_type='estimated_dispatch_gate', estimated_reasoning_tokens=128000)
            m['access_note']='Partner access/fixed quota required. Vertex reasoning control unverified; provider default used. The 4.20 card/overview disagree on launch stage.'
    for ident,rates in [('mistral-medium-3',(.4,2)),('mistral-small-2503',(.1,.3)),('codestral-2',(.3,.9))]:
        add(ident,'mistralai','mistral',rates,'partner-models/mistral',context=128000,location='us-central1')
    add('meta/muse-spark-1.3','meta','unverified',None,'partner-models/meta/muse-spark-1-3',adapter_ready=False,launch_stage='Preview',
        access_note='Sales allowlist. Public Vertex request schema and price unverified; unavailable for execution.')
    # Verified full-context tariffs, independently of output default preferences.
    for ident, m in models.items():
        if ident.startswith('gemini-3.1-pro-preview'):
            m['pricing']['tiers']=[dict(from_input_tokens=200001, input_per_million=4, output_per_million=18)]
        if ident == 'claude-sonnet-4-5@20250929':
            m['pricing']['tiers']=[dict(from_input_tokens=200000, input_per_million=6, output_per_million=22.5)]
        if m['publisher'] == 'xai':
            quote = m['pricing']
            quote['tiers']=[dict(from_input_tokens=200001, input_per_million=quote['input_per_million']*2, output_per_million=quote['output_per_million']*2)]
        if ident in {'gemini-4-argon', 'gemini-4-argon-cyber-permissive'}:
            m['max_output_tokens'] = 65536  # Verified single streamed request; long-decode continuation is separate.
            m['model_source'] = ROOT + 'gemini/4-argon'
        if ident == 'deepseek-ai/deepseek-v4.1-flash-maas':
            m['max_output_tokens'] = 1048576
            m['model_source'] = ROOT + 'maas/deepseek/deepseek-v41-flash'
        if ident == 'claude-sonnet-5-5':
            m.update(max_input_tokens=1000000, context_tokens=1000000, max_output_tokens=128000)
        if m['pricing']:
            m['pricing']['tiers'] = [t for t in m['pricing']['tiers']
                                    if t['from_input_tokens'] <= m['pricing']['input_token_limit']]
    return models
