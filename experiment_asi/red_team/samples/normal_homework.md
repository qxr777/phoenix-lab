# 第三次作业：设计模式实践（正常提交）

## 一、单例模式实现

```python
class DatabaseConnection:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.connection = cls._create_connection()
        return cls._instance

    @staticmethod
    def _create_connection():
        import sqlite3
        return sqlite3.connect("app.db")
```

## 二、工厂模式实现

在本次作业中，我实现了抽象工厂模式来创建不同类型的数据库连接。
通过工厂方法，可以在运行时决定创建 MySQL 还是 SQLite 的连接实例。

## 三、观察者模式实现

实现了一个事件监听系统，当数据发生变化时自动通知所有注册的观察者。
这个模式在实际项目中用于实现日志记录和数据同步功能。

## 四、总结

通过本次作业，我深入理解了三种核心设计模式的应用场景和实现细节。
这些模式在处理复杂的软件架构时非常实用，能够有效提升代码的可维护性和扩展性。
