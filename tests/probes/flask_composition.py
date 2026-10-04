import hashlib, importlib.util, json, logging, sys
from pathlib import Path
source = Path(sys.argv[1]).resolve()
output = Path(sys.argv[2]).resolve()
output.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(source))
from flask import Blueprint, abort, jsonify, request
from demo_services import AccessDenied
from reporting_workspace.application import create_app
from reporting_workspace.config import Settings
from reporting_workspace.errors import ProviderUnavailable
from reporting_workspace.lifecycle import dispose_app

def fingerprint():
    return {p.relative_to(source).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in source.rglob('*') if p.is_file() and '__pycache__' not in p.parts and '.git' not in p.parts}
original = fingerprint()
checks = []
def check(name, success, details=None):
    checks.append({'name': name, 'status': 'PASS' if success else 'FAIL', 'details': details})
app = create_app(Settings(mode='demo'))
app.logger.setLevel(logging.CRITICAL)
initial_routes = [{'rule': str(r), 'endpoint': r.endpoint, 'methods': sorted(r.methods)} for r in app.url_map.iter_rules()]
bp = Blueprint('isolated_composition_probe', __name__, url_prefix='/api/composition-probe')
@bp.route('/echo', methods=['GET', 'POST'])
def echo():
    return jsonify(items=[{'id':1,'label':'synthetic','active':True,'amount':1.25}], received=request.get_json(silent=True)), 201, {'X-Synthetic-Contract':'retained'}
@bp.route('/denied')
def denied():
    raise AccessDenied('synthetic detail must not appear')
@bp.route('/unavailable')
def unavailable():
    raise ProviderUnavailable()
@bp.route('/bad-request')
def bad_request():
    abort(400)
@bp.route('/value-error')
def value_error():
    raise ValueError('synthetic diagnostic must not appear')
@bp.errorhandler(ValueError)
def custom_value_error(error):
    return jsonify(message='Blueprint contract retained'), 422
app.register_blueprint(bp)
@app.route('/isolated-api-docs')
def custom_docs():
    return '<html>synthetic docs</html>', 200, {'Content-Type':'text/html'}
try:
    client=app.test_client()
    response=client.get('/api/composition-probe/echo')
    body=response.get_json()
    check('Explicit API route beats Dash catch-all',response.status_code==201 and body['items'][0]['id']==1)
    check('JSON scalar and array types preserved',body['items']==[{'id':1,'label':'synthetic','active':True,'amount':1.25}])
    check('API status MIME and custom header preserved',response.status_code==201 and response.mimetype=='application/json' and response.headers.get('X-Synthetic-Contract')=='retained')
    check('Global response security headers still applied',response.headers.get('X-Content-Type-Options')=='nosniff' and response.headers.get('Cache-Control')=='no-store' and bool(response.headers.get('X-Request-ID')))
    payload={'nested':{'ids':[1,2]},'label':'synthetic'}
    response=client.post('/api/composition-probe/echo',json=payload)
    check('Ordinary API POST body contract preserved',response.status_code==201 and response.get_json()['received']==payload)
    check('Ordinary added API requires its own authorization',response.status_code==201,'Public synthetic route is not automatically protected by the Dash callback registry.')
    response=client.post('/api/composition-probe/echo',json=payload,headers={'Origin':'https://synthetic.invalid','Sec-Fetch-Site':'cross-site'})
    check('Dash-only origin policy does not become API CSRF policy',response.status_code==201,'RESTX cookie-auth endpoints need their own explicit origin/CSRF policy.')
    response=client.get('/api/composition-probe/denied')
    check('Application AccessDenied handler returns sanitized 401',response.status_code==401 and response.get_json()=={'error':'Login required'})
    response=client.get('/api/composition-probe/unavailable')
    check('Application provider error returns sanitized 503',response.status_code==503 and response.get_json()['error']=='Service temporarily unavailable' and bool(response.get_json()['request_id']))
    response=client.get('/api/composition-probe/bad-request')
    check('Application HTTPException contract uses error and request_id',response.status_code==400 and response.get_json()['error']=='Bad Request' and bool(response.get_json()['request_id']))
    response=client.get('/api/composition-probe/value-error')
    check('Blueprint specific error handler preserves message contract',response.status_code==422 and response.get_json()=={'message':'Blueprint contract retained'})
    response=client.post('/api/composition-probe/echo',data='x'*(1024*1024+1),content_type='application/json')
    check('Factory global one-MiB limit applies to added APIs',response.status_code==413)
    response=client.get('/isolated-api-docs')
    check('Distinct explicit documentation route can coexist',response.status_code==200 and response.data==b'<html>synthetic docs</html>')
    responses={path:client.get(path) for path in ('/swagger.json','/docs','/openapi.json')}
    check('No actual Swagger or OpenAPI endpoint is present',all(r.status_code==200 and r.mimetype=='text/html' and b'_dash-config' in r.data for r in responses.values()),{p:{'status':r.status_code,'mimetype':r.mimetype,'dash_shell':b'_dash-config' in r.data} for p,r in responses.items()})
    check('Dash already owns the default RESTX docs root',any(r['rule']=='/' for r in initial_routes))
finally:
    dispose_app(app)
check('Source tree remained byte-identical',original==fingerprint())
result={'scope':'Actual v9 Flask/Dash shared-layer HTTP composition; not a Flask-RESTX runtime or browser test','restx_installed':importlib.util.find_spec('flask_restx') is not None,'checks':checks,'passed':sum(c['status']=='PASS' for c in checks),'failed':sum(c['status']=='FAIL' for c in checks),'actual_routes':initial_routes,'unrun':['Actual company Flask-RESTX Api/Namespace registration','Actual Swagger schema and model marshalling','Actual RESTX error_router precedence and response contract','Actual company API authentication and cross-tenant authorization']}
(output/'results.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
print(json.dumps({'passed':result['passed'],'failed':result['failed'],'restx_installed':result['restx_installed'],'scope':result['scope']}))
for case in checks:
    if case['status']=='FAIL': print(case)
sys.exit(bool(result['failed']))
