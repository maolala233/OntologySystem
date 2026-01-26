from fastapi import FastAPI
import uvicorn
app = FastAPI()

# 健康检查接口
@app.get('/health')
def get_health():
    return {'status': 'OK'}

if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=3001)