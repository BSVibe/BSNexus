import { test, expect } from '@playwright/test';
import { APIClient } from '../helpers/api-client';

test.describe('API Integration Tests - End-to-End Workflows', () => {
  const createdProjectIds: string[] = [];

  test.beforeAll(async () => {
    await APIClient.waitForBackend();
  });

  test.afterEach(async () => {
    for (const projectId of createdProjectIds) {
      try {
        await APIClient.deleteProject(projectId);
      } catch {
        // Ignore cleanup errors
      }
    }
    createdProjectIds.length = 0;
  });

  test('should create and retrieve a project', async () => {
    const projectData = {
      name: `Test Project ${Date.now()}`,
      description: 'Test project description',
      repo_path: '/test/repo',
    };

    const project = await APIClient.createProject(projectData);
    createdProjectIds.push(project.id);

    expect(project.id).toBeTruthy();
    expect(project.name).toBe(projectData.name);
    expect(project.description).toBe(projectData.description);
    expect(project.status).toBe('design');
  });

  test('should list all projects', async () => {
    const project1 = await APIClient.createProject({
      name: `Project 1 ${Date.now()}`,
      description: 'Project 1',
      repo_path: '/repo1',
    });
    const project2 = await APIClient.createProject({
      name: `Project 2 ${Date.now()}`,
      description: 'Project 2',
      repo_path: '/repo2',
    });
    createdProjectIds.push(project1.id, project2.id);

    const projects = await APIClient.listProjects();

    expect(Array.isArray(projects)).toBe(true);
    expect(projects.length).toBeGreaterThanOrEqual(2);

    const projectIds = projects.map((p: Record<string, unknown>) => p.id);
    expect(projectIds).toContain(project1.id);
    expect(projectIds).toContain(project2.id);
  });

  test('should delete a project', async () => {
    const project = await APIClient.createProject({
      name: `Delete Test ${Date.now()}`,
      description: 'To be deleted',
      repo_path: '/test/delete',
    });

    const projectsBefore = await APIClient.listProjects();
    const countBefore = projectsBefore.length;

    await APIClient.deleteProject(project.id);

    const projectsAfter = await APIClient.listProjects();
    const countAfter = projectsAfter.length;

    expect(countAfter).toBeLessThan(countBefore);

    const exists = projectsAfter.some((p: Record<string, unknown>) => p.id === project.id);
    expect(exists).toBe(false);
  });

  test('should create phase in project', async () => {
    const project = await APIClient.createProject({
      name: `Phase Test ${Date.now()}`,
      description: 'Project with phases',
      repo_path: '/test/phases',
    });
    createdProjectIds.push(project.id);

    const phase = await APIClient.createPhase(project.id, {
      name: 'Phase 1',
      description: 'First phase',
      order: 1,
    });

    expect(phase.id).toBeTruthy();
    expect(phase.name).toBe('Phase 1');
    expect(phase.project_id).toBe(project.id);
  });

  test('should create task in phase', async () => {
    const project = await APIClient.createProject({
      name: `Task Test ${Date.now()}`,
      description: 'Project for task testing',
      repo_path: '/test/tasks',
    });
    createdProjectIds.push(project.id);

    const phase = await APIClient.createPhase(project.id, {
      name: 'Phase 1',
      description: 'Test phase',
      order: 1,
    });
    await APIClient.activatePhase(project.id, phase.id);

    const task = await APIClient.createTask({
      project_id: project.id,
      phase_id: phase.id,
      title: `Task ${Date.now()}`,
      description: 'Test task',
      priority: 'medium',
      worker_prompt: 'Do work',
      qa_prompt: 'Check work',
    });

    expect(task.id).toBeTruthy();
    expect(task.title).toBeTruthy();
    expect(task.project_id).toBe(project.id);
    expect(task.phase_id).toBe(phase.id);
  });

  test('should transition task through states', async () => {
    const project = await APIClient.createProject({
      name: `Transition Test ${Date.now()}`,
      description: 'Task transitions',
      repo_path: '/test/transitions',
    });
    createdProjectIds.push(project.id);

    const phase = await APIClient.createPhase(project.id, {
      name: 'Phase 1',
      description: 'Test phase',
      order: 1,
    });
    await APIClient.activatePhase(project.id, phase.id);

    const task = await APIClient.createTask({
      project_id: project.id,
      phase_id: phase.id,
      title: `Transition Task ${Date.now()}`,
      description: 'Transition test',
      priority: 'medium',
      worker_prompt: 'Work',
      qa_prompt: 'QA',
    });

    // Task should start in ready state
    const currentTask = await APIClient.getTask(task.id);
    expect(currentTask.status).toBe('ready');
    expect(currentTask.version).toBe(1);

    // Transition to in_progress
    let transitioned = await APIClient.transitionTask(task.id, {
      new_status: 'in_progress',
      actor: 'test',
      expected_version: currentTask.version,
    });
    expect(transitioned.status).toBe('in_progress');
    expect(transitioned.version).toBe(2);

    // Transition to review
    transitioned = await APIClient.transitionTask(task.id, {
      new_status: 'review',
      actor: 'test',
      expected_version: transitioned.version,
    });
    expect(transitioned.status).toBe('review');
    expect(transitioned.version).toBe(3);

    // Transition to done
    transitioned = await APIClient.transitionTask(task.id, {
      new_status: 'done',
      actor: 'test',
      expected_version: transitioned.version,
    });
    expect(transitioned.status).toBe('done');
    expect(transitioned.version).toBe(4);
  });

  test('should handle task dependencies', async () => {
    const project = await APIClient.createProject({
      name: `Dependencies Test ${Date.now()}`,
      description: 'Task dependencies',
      repo_path: '/test/dependencies',
    });
    createdProjectIds.push(project.id);

    const phase = await APIClient.createPhase(project.id, {
      name: 'Phase 1',
      description: 'Test phase',
      order: 1,
    });

    // Create task A (no dependencies)
    const taskA = await APIClient.createTask({
      project_id: project.id,
      phase_id: phase.id,
      title: `Task A ${Date.now()}`,
      description: 'Task A',
      priority: 'medium',
      worker_prompt: 'Work',
      qa_prompt: 'QA',
    });

    // Create task B (depends on A)
    const taskB = await APIClient.createTask({
      project_id: project.id,
      phase_id: phase.id,
      title: `Task B ${Date.now()}`,
      description: 'Task B',
      priority: 'medium',
      depends_on: [taskA.id],
      worker_prompt: 'Work',
      qa_prompt: 'QA',
    });

    // Task A should be ready
    const a = await APIClient.getTask(taskA.id);
    expect(a.status).toBe('ready');

    // Task B should be waiting
    let b = await APIClient.getTask(taskB.id);
    expect(b.status).toBe('waiting');

    // Complete task A
    await APIClient.transitionTask(taskA.id, { new_status: 'in_progress', actor: 'test' });
    await APIClient.transitionTask(taskA.id, { new_status: 'review', actor: 'test' });
    await APIClient.transitionTask(taskA.id, { new_status: 'done', actor: 'test' });

    // Task B should now be ready
    b = await APIClient.getTask(taskB.id);
    expect(b.status).toBe('ready');
  });

  test('should get board state with task counts', async () => {
    const project = await APIClient.createProject({
      name: `Board Test ${Date.now()}`,
      description: 'Board testing',
      repo_path: '/test/board',
    });
    createdProjectIds.push(project.id);

    const phase = await APIClient.createPhase(project.id, {
      name: 'Phase 1',
      description: 'Test phase',
      order: 1,
    });

    // Create tasks
    await APIClient.createTask({
      project_id: project.id,
      phase_id: phase.id,
      title: `Task 1 ${Date.now()}`,
      description: 'Task 1',
      priority: 'medium',
      worker_prompt: 'Work',
      qa_prompt: 'QA',
    });

    const task2 = await APIClient.createTask({
      project_id: project.id,
      phase_id: phase.id,
      title: `Task 2 ${Date.now()}`,
      description: 'Task 2',
      priority: 'medium',
      worker_prompt: 'Work',
      qa_prompt: 'QA',
    });

    // Move task2 to in_progress
    await APIClient.transitionTask(task2.id, { new_status: 'in_progress', actor: 'test' });

    // Get board
    const board = await APIClient.getBoard(project.id);

    expect(board.project_id).toBe(project.id);
    expect(board.columns).toBeTruthy();
    expect(board.stats).toBeTruthy();

    // Verify task distribution
    const readyTasks = board.columns.ready?.tasks || [];
    const inProgressTasks = board.columns.in_progress?.tasks || [];

    expect(readyTasks.length).toBeGreaterThan(0);
    expect(inProgressTasks.length).toBeGreaterThan(0);
    expect(board.stats.total).toBeGreaterThanOrEqual(2);
  });

  test('should reject invalid transitions', async () => {
    const project = await APIClient.createProject({
      name: `Invalid Transition Test ${Date.now()}`,
      description: 'Invalid transitions',
      repo_path: '/test/invalid',
    });
    createdProjectIds.push(project.id);

    const phase = await APIClient.createPhase(project.id, {
      name: 'Phase 1',
      description: 'Test phase',
      order: 1,
    });
    await APIClient.activatePhase(project.id, phase.id);

    const task = await APIClient.createTask({
      project_id: project.id,
      phase_id: phase.id,
      title: `Task ${Date.now()}`,
      description: 'Test task',
      priority: 'medium',
      worker_prompt: 'Work',
      qa_prompt: 'QA',
    });

    // Try invalid transition from ready -> done (should be invalid)
    try {
      await APIClient.transitionTask(task.id, {
        new_status: 'done',
        actor: 'test',
      });
      expect.fail('Should have thrown an error for invalid transition');
    } catch (e) {
      expect(e).toBeTruthy();
    }
  });

  test('should enforce optimistic locking with version conflicts', async () => {
    const project = await APIClient.createProject({
      name: `Version Conflict Test ${Date.now()}`,
      description: 'Version conflicts',
      repo_path: '/test/version',
    });
    createdProjectIds.push(project.id);

    const phase = await APIClient.createPhase(project.id, {
      name: 'Phase 1',
      description: 'Test phase',
      order: 1,
    });
    await APIClient.activatePhase(project.id, phase.id);

    const task = await APIClient.createTask({
      project_id: project.id,
      phase_id: phase.id,
      title: `Task ${Date.now()}`,
      description: 'Test task',
      priority: 'medium',
      worker_prompt: 'Work',
      qa_prompt: 'QA',
    });

    // Try transition with wrong version
    try {
      await APIClient.transitionTask(task.id, {
        new_status: 'in_progress',
        actor: 'test',
        expected_version: 999,
      });
      expect.fail('Should have thrown version conflict error');
    } catch (e) {
      expect(e).toBeTruthy();
    }
  });

  test('should get projects summary for dashboard', async () => {
    const project = await APIClient.createProject({
      name: `Summary Test ${Date.now()}`,
      description: 'Summary testing',
      repo_path: '/test/summary',
    });
    createdProjectIds.push(project.id);

    const phase = await APIClient.createPhase(project.id, {
      name: 'Phase 1',
      description: 'Test phase',
      order: 1,
    });

    // Create tasks in different states
    await APIClient.createTask({
      project_id: project.id,
      phase_id: phase.id,
      title: `Task 1 ${Date.now()}`,
      description: 'Task 1',
      priority: 'medium',
      worker_prompt: 'Work',
      qa_prompt: 'QA',
    });

    const task2 = await APIClient.createTask({
      project_id: project.id,
      phase_id: phase.id,
      title: `Task 2 ${Date.now()}`,
      description: 'Task 2',
      priority: 'medium',
      worker_prompt: 'Work',
      qa_prompt: 'QA',
    });

    // Move task2 to done
    await APIClient.transitionTask(task2.id, { new_status: 'in_progress', actor: 'test' });
    await APIClient.transitionTask(task2.id, { new_status: 'review', actor: 'test' });
    await APIClient.transitionTask(task2.id, { new_status: 'done', actor: 'test' });

    const summary = await APIClient.getProjectsSummary();

    expect(Array.isArray(summary)).toBe(true);

    const projectSummary = summary.find((s: Record<string, unknown>) => s.id === project.id);
    expect(projectSummary).toBeTruthy();
    expect(projectSummary.task_counts).toBeTruthy();
    expect(projectSummary.task_counts['done']).toBe(1);
    expect(projectSummary.task_counts['ready']).toBe(1);
  });

  test('should complete full project workflow', async () => {
    // Create project
    const project = await APIClient.createProject({
      name: `Full Workflow ${Date.now()}`,
      description: 'Complete workflow test',
      repo_path: '/test/workflow',
    });
    createdProjectIds.push(project.id);

    // Create phases
    const phase1 = await APIClient.createPhase(project.id, {
      name: 'Phase 1',
      description: 'Phase 1 description',
      order: 1,
    });
    await APIClient.activatePhase(project.id, phase1.id);

    const phase2 = await APIClient.createPhase(project.id, {
      name: 'Phase 2',
      description: 'Phase 2 description',
      order: 2,
    });
    await APIClient.activatePhase(project.id, phase2.id);

    // Create tasks in phase 1
    const task1a = await APIClient.createTask({
      project_id: project.id,
      phase_id: phase1.id,
      title: `Phase 1 - Task A ${Date.now()}`,
      description: 'Task 1A',
      priority: 'medium',
      worker_prompt: 'Work',
      qa_prompt: 'QA',
    });

    const task1b = await APIClient.createTask({
      project_id: project.id,
      phase_id: phase1.id,
      title: `Phase 1 - Task B ${Date.now()}`,
      description: 'Task 1B',
      priority: 'medium',
      worker_prompt: 'Work',
      qa_prompt: 'QA',
    });

    // Create task in phase 2 that depends on both phase 1 tasks
    const task2a = await APIClient.createTask({
      project_id: project.id,
      phase_id: phase2.id,
      title: `Phase 2 - Task A ${Date.now()}`,
      description: 'Task 2A',
      priority: 'medium',
      depends_on: [task1a.id, task1b.id],
      worker_prompt: 'Work',
      qa_prompt: 'QA',
    });

    // Complete phase 1 tasks
    for (const taskId of [task1a.id, task1b.id]) {
      let task = await APIClient.getTask(taskId);
      await APIClient.transitionTask(taskId, {
        new_status: 'in_progress',
        actor: 'test',
        expected_version: task.version,
      });

      task = await APIClient.getTask(taskId);
      await APIClient.transitionTask(taskId, {
        new_status: 'review',
        actor: 'test',
        expected_version: task.version,
      });

      task = await APIClient.getTask(taskId);
      await APIClient.transitionTask(taskId, {
        new_status: 'done',
        actor: 'test',
        expected_version: task.version,
      });
    }

    // Verify phase 2 task is now ready (dependencies satisfied)
    const task2aFinal = await APIClient.getTask(task2a.id);
    expect(task2aFinal.status).toBe('ready');

    // Get final board state
    const board = await APIClient.getBoard(project.id);
    expect(board.stats.done).toBe(2);
    expect(board.stats.ready).toBe(1);
  });
});
