"""
Pull relevant info from the indivo_server codebase to generate a framework for Indivo documentation
"""

# Add indivo_server to the sys path so that our script can find the codebase
# and Django can find settings.py
import sys, os
os.chdir('../..')
sys.path.append(os.getcwd())
sys.path.append("%s/.."%os.getcwd())
os.environ['DJANGO_SETTINGS_MODULE'] = 'settings'

from django.conf import settings
from operator import attrgetter

from indivo.accesscontrol.access_rule import AccessRule
import indivo
import re

API_ROOTDIR = 'indivo_server'
API_FP = 'doc/sphinx/autogen/api'
API_EXT = '.py'
API_CALLS_DICT = 'CALLS'

class APIDict(object):
    ''' 
    A dictionary for holding Indivo API calls. If specified,
    auto-loads the calls from a python file. 
    '''

    api_rootdir = API_ROOTDIR
    api_fp = API_FP
    api_ext = API_EXT
    calls_dict = API_CALLS_DICT

    def __init__(self, read_file=True):
        self.apicache = {}
        self.dirty = False
        if read_file:
            self._read_api()

    def is_empty(self):
        return not self.apicache

    def _read_api(self):
        '''
        Reads in the calls from the API file.
        '''
        calls = {}
        try:
            importstr = ("%s/%s"%(self.api_rootdir, self.api_fp)).replace('/', '.')
            api = __import__(importstr, fromlist=[self.calls_dict])
            calls = getattr(api, self.calls_dict)
        except ImportError: # file doesn't exist yet
            pass
        except AttributeError: # file doesn't have the calls_dict variable, must be bad formatting
            raise ValueError('module %s does not contain variable %s, and cannot be parsed as API calls'%
                             (importstr,self.calls_dict))

        for call in calls:
            c_obj = Call(call['path'], call['method'], call['view_func'],
                         url_params=call['url_params'], query_opts=call['query_opts'], 
                         data_fields=call['data_fields'], description=call['description'])
            self.apicache[c_obj.title] = c_obj # don't use our __setitem__: userfile shouldn't dirty the our cache
        
    def _write_api(self):
        '''
        Write the current state of the API to the API file
        '''
        full_fp = '%s/%s%s'%(settings.APP_HOME,self.api_fp, self.api_ext)
        f = open(full_fp, 'w')

        calls = ',\n'.join([c.to_python() for c in sorted(self.values(), key=attrgetter('path', 'method'))])
        imports = '''
from indivo.views import *
from codingsystems.views import *
from django.views.static import serve
'''
        out = "%s\n\nCALLS =[%s]"%(imports,calls)
        f.write(out)
        f.close()
    
    def get(self, key, default=None):
        try:
            return self.__getitem__(key)
        except KeyError:
            return default

    def __getitem__(self, key):
        return self.apicache[key]

    def __setitem__(self, key, val):
        oldval = self.get(key, None)
        self.apicache[key] = val
        self.dirty = oldval != val

    def __delitem__(self, key):
        del self.apicache[key]
        self.dirty = True

    def save(self):
        if self.dirty:
            self._write_api()

    def update(self, call_dict):
        for k, v in call_dict.iteritems():
            self[k] = v

    def keys(self):
        return self.apicache.keys()
    
    def values(self):
        return self.apicache.values()

    def __repr__(self):
        dictrepr = dict.__repr__(self.apicache)
        return '%s(%s)' % (type(self).__name__, dictrepr)

class CallResolver(object):
    '''
    Takes a user-entered API call and an auto-generated API call
    and resolves their differing properties. Used in generating
    merged API calls.
    '''
    def __init__(self, cp_call, user_call, user_preferred=True):
        self.cp_call = cp_call
        self.user_call = user_call
        self.user_preferred = user_preferred

    def prefer_user(self):
        self.user_preferred = True

    def prefer_cp(self):
        self.user_preferred = False
    
    def resolve(self, field, defaults=None):
        '''
        Given an API call field, returns the value of that field
        that a merged call should use.
        '''
        if isinstance(getattr(self.cp_call, field, None), dict):
            return self._resolve_dictfield(field, defaults)
        else:
            return self._resolve_textfield(field, defaults)

    def _resolve_dictfield(self, field, defaults=None):
        cp_dict = getattr(self.cp_call, field)
        user_dict = getattr(self.user_call, field)

        all_keys = set(cp_dict.keys()).union(set(user_dict.keys()))

        retdict = {}
        for key in all_keys:
            cp_val = cp_dict.get(key, None)
            user_val = user_dict.get(key, None)
            retdict[key] = self._resolve(cp_val, user_val, key, defaults)

        return retdict

    def _resolve_textfield(self, field, defaults=None):
        cp_val = getattr(self.cp_call, field, None)
        user_val = getattr(self.user_call, field, None)
        return self._resolve(cp_val, user_val, field, defaults)
        
    def _resolve(self, cp_val, user_val, default_key=None, defaults=None):
        
        if (self.user_preferred and user_val) or not cp_val:
            retval = user_val
        else:
            retval = cp_val

        if not retval and defaults and defaults.has_key(default_key):
            retval = defaults[default_key]
        
        if retval == None:
            retval = ''
            
        return retval

class Call(object):
    '''
    Representation of an Indivo API call, with rendering to ReST and Python.
    '''

    def __setattr__(self, name, value):
        object.__setattr__(self, name, value)
        if name == 'view_func' and value:
            self.access_rule = self._get_access_rule()
        if name == 'access_rule' and value:
            self.access_doc = self.access_rule.rule.__doc__

    def __init__(self, path=None, method=None, view_func=None,
                 url_params={}, query_opts={}, data_fields={}, description=''):
        self.path = path
        self.method = method
        self.title = '%s %s'%(method, path)
        self.view_func = view_func
        self.access_rule = self._get_access_rule()
        self.access_doc = self.access_rule.rule.__doc__ if self.access_rule else ''
        self.url_params = url_params
        self.query_opts = query_opts
        self.data_fields = data_fields
        self.description = description

    def _print_dict(self, d):
        lines = []
        for key, value in d.iteritems():
            lines.append("%s'%s':'%s',\n"%(self._indent(8), key, value))
        out = '{\n%s%s}'%(''.join(lines), self._indent(8))
        return out

    def set_defaults(self, default_maps):
        ''' Replace blank attributes of the Call with default values provided in `default_maps` '''
        for fieldname, default_map in default_maps.iteritems():
            if hasattr(self, fieldname):
                fieldval = getattr(self, fieldname, None)
            
                # The attribute is a dict, look for its keys in the map
                if isinstance(fieldval, dict):
                    for k in fieldval.keys():
                        if default_map.has_key(k) and not fieldval[k]:
                            fieldval[k] = default_map[k]

                # The attribute is a string, look for the attribute itself in the map
                else:
                    if default_map.has_key(fieldname) and not fieldval:
                        setattr(self, fieldname, default_map[fieldname])
            
    def to_python(self):
        ''' 
        Render the Call to python code, for easy import.
        Output will look like:
        
        {
          'method': 'get',
          'path': '/records/{RECORD_ID}',
          'url_params': {
                          'RECORD_ID':'the Indivo record identifier',
                        },
          'view_func': record, # THIS IS A FUNCTION REFERENCE, NOT A STRING
          'query_opts' : {
                          'offset': 'offset number. default is 0',
                         },
          'data_fields': {
                         },
          'description':'Get basic record information'
        }
        '''
        method = '"method":"%s",\n'%self.method
        path = '"path":"%s",\n'%self.path
        view_func = '"view_func":%s,\n'%self.view_func.__name__ # don't quote it: this is a variable reference
        access_rule = '"access_doc":"%s",\n'%self.access_doc
        url_params = '"url_params":%s,\n'%self._print_dict(self.url_params)
        query_opts = '"query_opts":%s,\n'%self._print_dict(self.query_opts)
        data_fields = '"data_fields":%s,\n'%self._print_dict(self.data_fields)
        description = '"description":"%s",\n'%self.description
        indent = 4
        
        out = "{\n%s\n}"%( 
                             self._indent(indent).join([method, path, view_func, access_rule, 
                                                        url_params, query_opts, data_fields, description]))
        return out

    def to_ReST(self):
        # Output will look like 
        #
        # .. http:get:: /records/{RECORD_ID}
        #
        #    Get basic record information.
        #
        #    :shortname: record
        #    :accesscontrol: The record owner, the admin app that created it, or an app with access to it
        #    :query order_by: one of ``label``, ``created-at``
        #    :query offset: offset number. default is 0
        #    :query limit: limit number. default is 30
        #    :param RECORD_ID: the Indivo record identifier
        

        directive = ".. http:%s:: %s"%(self.method.lower(), self.path)
        short_name = ":shortname: %s"%self.view_func.__name__
        access_doc = ":accesscontrol: %s"%self.access_doc
        url_params = [":parameter %s: %s"%(p, self.url_params[p]) for p in self.url_params.keys()]
        query_opts = [":queryparameter %s: %s"%(q, self.query_opts[q]) for q in self.query_opts.keys()]
        data_fields = [":formparameter %s: %s"%(d, self.data_fields[d]) for d in self.data_fields.keys()]
        indent = 3
        
        out = '%s\n%s\n%s%s%s%s%s'%(self._list_to_ReST([directive], 0),
                                  self._list_to_ReST([self.description], indent),
                                  self._list_to_ReST([short_name], indent), 
                                  self._list_to_ReST([access_doc], indent),
                                  self._list_to_ReST(url_params, indent),
                                  self._list_to_ReST(query_opts, indent),
                                  self._list_to_ReST(data_fields, indent)
                                )
        return out

    def _indent(self, indent):
        return " "*indent

    def _list_to_ReST(self, l, indent):
        out = ''
        for item in l:
            out += '%s%s\n'%(self._indent(indent), item)
        return out

    def _get_access_rule(self):
        return AccessRule.lookup(self.view_func) if self.view_func else None

class CallParser(object):
    def __init__(self, urls):
        self.urllist = urls
        self.api = APIDict(read_file=False)
        self.parse(self.urllist, parent_path='/')

    def register(self, call):
        self.api[call.title] = call

    def lookup(self, call):
        return self.api.get(call.title, None)

    def parse(self, urllist, parent_path=''):
        for entry in urllist:
                
            # not a leaf node, recurse
            if hasattr(entry, 'url_patterns'):
                cur_path = entry.regex.pattern[1:]
                self.parse(entry.url_patterns, parent_path+cur_path)
                    
            # leaf node
            else:
                path, url_params = self.parse_url_params(parent_path + entry.regex.pattern[1:-1])

                # build up url_params for the Call constructor
                params = {}
                for param in url_params:
#                    desc = URL_PARAM_DESC.get(param[1], '')
                    params[param[1]] = ''

                if isinstance(entry.callback, indivo.lib.utils.MethodDispatcher):
                    for method, view_func in entry.callback.methods.iteritems():
                        call = Call(path, method, view_func=view_func, url_params=params)
                        self.register(call)
                else:
                    method = 'GET'
                    call = Call(path, method, entry.callback, url_params=params)
                    self.register(call)

    def _get_url_params(self, url):
        params = []
        # match things inside <> that are in the context of (?P< your match >..) 
        for pattern in re.finditer( '\(\?P<(.*?)>.*?\)', url):
            match = pattern.group(0)
            param = pattern.group(1).upper()
            params.append((match, param))

        return params

    def parse_url_params(self, url):
        params = self._get_url_params(url)
        for param in params:
            url = url.replace(param[0], '{%s}'%param[1])
        return url, params
