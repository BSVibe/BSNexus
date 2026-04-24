import { useState, useEffect } from 'react';
import './TodoApp.css';

const API_URL = 'http://localhost:8000/api/todos';

function TodoApp() {
  // 할 일 목록 상태
  const [todos, setTodos] = useState([]);
  // 입력 필드 상태
  const [inputValue, setInputValue] = useState('');

  // 컴포넌트 마운트 시 할 일 목록 불러오기
  useEffect(() => {
    fetchTodos();
  }, []);

  // 할 일 목록 가져오기
  const fetchTodos = async () => {
    try {
      const response = await fetch(API_URL);
      if (!response.ok) throw new Error('데이터를 가져오는데 실패했습니다.');
      const data = await response.json();
      setTodos(data);
    } catch (error) {
      console.error('Error fetching todos:', error);
    }
  };

  // 할 일 추가
  const addTodo = async (e) => {
    e.preventDefault();
    if (!inputValue.trim()) return;

    try {
      const response = await fetch(API_URL, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ text: inputValue, completed: false }),
      });
      if (!response.ok) throw new Error('할 일을 추가하는데 실패했습니다.');
      
      // 새로운 할 일 목록 다시 불러오기 (또는 상태 업데이트 최적화 가능)
      fetchTodos();
      setInputValue('');
    } catch (error) {
      console.error('Error adding todo:', error);
    }
  };

  // 할 일 완료 토글
  const toggleTodo = async (id) => {
    const todoToToggle = todos.find((t) => t.id === id);
    if (!todoToToggle) return;

    const updatedTodo = { ...todoToToggle, completed: !todoToToggle.completed };

    try {
      const response = await fetch(`${API_URL}/${id}`, {
        method: 'PUT', // 또는 PATCH
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(updatedTodo),
      });
      if (!response.ok) throw new Error('상태 변경에 실패했습니다.');

      // 상태 업데이트
      setTodos((prev) =>
        prev.map((todo) => (todo.id === id ? updatedTodo : todo))
      );
    } catch (error) {
      console.error('Error toggling todo:', error);
    }
  };

  // 할 일 삭제
  const deleteTodo = async (id) => {
    if (!window.confirm('정말로 이 할 일을 삭제하시겠습니까?')) return;

    try {
      const response = await fetch(`${API_URL}/${id}`, {
        method: 'DELETE',
      });
      if (!response.ok) throw new Error('삭제에 실패했습니다.');

      // 상태 업데이트
      setTodos((prev) => prev.filter((todo) => todo.id !== id));
    } catch (error) {
      console.error('Error deleting todo:', error);
    }
  };

  return (
    <div className="todo-container">
      <h2>할 일 목록</h2>
      
      {/* 입력 폼 */}
      <form onSubmit={addTodo} className="todo-form">
        <input
          type="text"
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          placeholder="새로운 할 일을 입력하세요..."
          className="todo-input"
        />
        <button type="submit" className="add-btn">
          추가
        </button>
      </form>

      {/* 할 일 리스트 */}
      <ul className="todo-list">
        {todos.length === 0 ? (
          <li className="empty-message">할 일이 없습니다.</li>
        ) : (
          todos.map((todo) => (
            <li key={todo.id} className={`todo-item ${todo.completed ? 'completed' : ''}`}>
              <label className="todo-label">
                <input
                  type="checkbox"
                  checked={todo.completed}
                  onChange={() => toggleTodo(todo.id)}
                  className="todo-checkbox"
                />
                <span className="todo-text">{todo.text}</span>
              </label>
              <button
                onClick={() => deleteTodo(todo.id)}
                className="delete-btn"
                aria-label="삭제"
              >
                삭제
              </button>
            </li>
          ))
        )}
      </ul>
    </div>
  );
}

export default TodoApp;