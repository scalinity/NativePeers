"""Separate native Claude API errors from inference model observations."""
from .provider_errors import diagnostic,classify


class Evidence:
    def __init__(self):
        self.synthetic=[];self.model_output=False;self.tools=False;self.rounds=0

    def observe(self,event):
        message=event.get('message',{})
        synthetic=(event.get('isApiErrorMessage') is True or message.get('model')=='<synthetic>')
        if event.get('type')=='assistant':
            self.rounds+=1
            if synthetic:self.synthetic.append(event)
            else:self.model_output=True
        for content in message.get('content',[]) if isinstance(message.get('content'),list) else []:
            if content.get('type') in ('tool_use','tool_result','server_tool_use'):self.tools=True
        return synthetic

    def failure(self,terminal,exit_code):
        terminal_observed=terminal is not None
        terminal=terminal or {}
        terminal_failed=terminal.get('is_error') is True or terminal.get('subtype')!='success' or exit_code!=0
        if not terminal_failed and self.model_output:return None
        failed=self.synthetic or terminal.get('is_error') or terminal.get('subtype')!='success' or exit_code!=0
        if not failed:return None
        code=classify(terminal)
        if code=='PROVIDER_TASK_FAILED':
            code=next((value for event in reversed(self.synthetic) if (value:=classify(event))!='PROVIDER_TASK_FAILED'),code)
        error=diagnostic({'api_errors':self.synthetic,'terminal':terminal,'exit_code':exit_code},submitted=True,code=code)
        usages=[e.get('message',{}).get('usage',{}) for e in self.synthetic]
        terminal_usage=terminal.get('usage')
        if terminal_usage is not None:usages.append(terminal_usage)
        zero=bool(usages) and all(isinstance(u,dict) and u.get('input_tokens')==0 and u.get('output_tokens')==0
                                   and not u.get('cache_creation_input_tokens',0) and not u.get('cache_read_input_tokens',0)
                                   and not any(u.get('server_tool_use',{}).values()) for u in usages)
        final_text=terminal.get('result')
        diagnostic_texts={'\n'.join(c['text'] for c in event.get('message',{}).get('content',[])
                           if c.get('type')=='text' and isinstance(c.get('text'),str)).strip() for event in self.synthetic}
        final_may_be_output=bool(final_text) and (not isinstance(final_text,str) or final_text.strip() not in diagnostic_texts)
        explicit_failure=terminal.get('is_error') is True or str(terminal.get('subtype','')).startswith('error')
        safe=terminal_observed and explicit_failure and not final_may_be_output and bool(self.synthetic) and zero and not self.model_output and not self.tools and error['code'] in ('QUOTA_EXHAUSTED','AUTH_REQUIRED')
        error['execution']={'input_submitted':True,'model_output_observed':self.model_output,
                            'tool_activity_observed':self.tools,'zero_usage_reported':zero,
                            'terminal_result_may_be_output':final_may_be_output,
                            'definitive_no_execution':safe,'terminal_observed':terminal_observed}
        return error
