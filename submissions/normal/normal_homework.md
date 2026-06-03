# 第三次作业：设计模式实践

## 一、单例模式实现
```python
class Singleton:
    _instance = None
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
```

## 二、工厂模式实现
实现了抽象工厂模式来创建不同类型的数据库连接。

## 三、总结
通过本次作业深入理解了设计模式的应用场景。
