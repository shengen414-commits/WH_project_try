# 1. 指定基础镜像：直接用配置好 Python 的官方镜像
FROM python:3.10-slim

# 2. 设置容器内部的工作目录：接下来的操作都在这里进行
WORKDIR /app

# 3. 将本地电脑的依赖文件拷贝到容器里
COPY requirements.txt .

# 4. 在容器里安装依赖库
RUN pip install --no-cache-dir -r requirements.txt

# 5. 将当前目录下的所有代码拷贝到容器的 /app 目录下
COPY . .

# 6. 容器启动时执行的命令
CMD ["python", "app.py"]