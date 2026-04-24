import { useState, useEffect } from 'react';
import axios from 'axios';
import './App.css';

const API_URL = 'http://localhost:8000/tasks';

function App() {
  const [tasks, setTasks] = useState([]);
  const [newTask, setNewTask] = useState('');
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState(null);

  // 1. Fetch tasks from backend on mount
  useEffect(() => {
    fetchTasks();
  }, []);

  const fetchTasks = async () => {
    try {
      const response = await axios.get(API_URL);
      setTasks(response.data);
    } catch (err) {
      setError('Failed to load tasks. Please ensure the backend is running.');
      console.error(err);
    } finally {
      setIsLoading(false);
    }
  };

  // 2. Add a new task
  const addTask = async () => {
    if (!newTask.trim()) return;

    try {
      const response = await axios.post(API_URL, { title: newTask });
      setTasks([...tasks, response.data]);
      setNewTask('');
    } catch (err) {
      setError('Failed to add task.');
      console.error(err);
    }
  };

  // 3. Toggle task completion status
  const toggleTask = async (id) => {
    const taskToToggle = tasks.find((t) => t.id === id);
    if (!taskToToggle) return;

    try {
      await axios.put(`${API_URL}/${id}`, { 
        completed: !taskToToggle.completed 
      });
      // Update local state to reflect change immediately
      setTasks(tasks.map(task => 
        task.id === id ? { ...task, completed: !task.completed } : task
      ));
    } catch (err) {
      setError('Failed to update task.');
      console.error(err);
    }
  };

  return (
    <div className="app-container">
      <h1>Todo List</h1>
      
      <div className="input-section">
        <input
          type="text"
          value={newTask}
          onChange={(e) => setNewTask(e.target.value)}
          onKeyPress={(e) => e.key === 'Enter' && addTask()}
          placeholder="Add a new task..."
        />
        <button onClick={addTask}>Add</button>
      </div>

      {error && <div className="error-message">{error}</div>}

      {isLoading ? (
        <p>Loading tasks...</p>
      ) : (
        <ul>
          {tasks.map((task) => (
            <li key={task.id} className={task.completed ? 'completed' : ''}>
              <span onClick={() => toggleTask(task.id)}>
                {task.title}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default App;